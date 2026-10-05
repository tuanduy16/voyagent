"""CLI entry point.

Example:
    python -m voyagent --origin "Boston, USA" --destination "Lisbon, Portugal" \
        --start 2026-11-10 --end 2026-11-16 --budget 4000 --travelers 2 \
        --interests food history nightlife --open
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import webbrowser
from datetime import date
from pathlib import Path

import anthropic
from pydantic import ValidationError

from . import config
from .agent import AgentError
from .models import TripRequest
from .orchestrator import TravelOrchestrator
from .render import render_html, render_markdown


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="voyagent", description="Voyagent: multi-agent travel planner powered by Claude")
    p.add_argument("--origin", required=True, help="Departure city, e.g. 'Boston, USA'")
    p.add_argument("--destination", required=True, help="Destination city or country, e.g. 'Lisbon, Portugal'")
    p.add_argument("--start", required=True, type=date.fromisoformat, help="Departure date YYYY-MM-DD")
    p.add_argument("--end", required=True, type=date.fromisoformat, help="Return date YYYY-MM-DD")
    p.add_argument("--budget", required=True, type=float, help="Total budget for all travelers")
    p.add_argument("--currency", default="USD")
    p.add_argument("--travelers", type=int, default=1)
    p.add_argument("--interests", nargs="*", default=[], help="e.g. food history nightlife nature")
    p.add_argument("--style", choices=["budget", "balanced", "luxury"], default="balanced")
    p.add_argument("--notes", default="", help="Free-text preferences, e.g. 'no red-eyes, vegetarian'")
    p.add_argument(
        "--subscription", action="store_true",
        help="Run agents through Claude Code (`claude -p`) on your Claude Pro/Max login instead of an API key",
    )
    p.add_argument("--no-web-search", action="store_true", help="Disable Claude web search (cheaper, less accurate)")
    p.add_argument("--revisions", type=int, default=1, help="Max budget-revision rounds (default 1)")
    p.add_argument("--out", default="output", help="Output directory")
    p.add_argument("--open", action="store_true", help="Open the HTML plan in a browser when done")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    if args.subscription:
        config.BACKEND = "subscription"
    try:
        req = TripRequest(
            origin=args.origin, destination=args.destination, start_date=args.start, end_date=args.end,
            budget=args.budget, currency=args.currency, travelers=args.travelers, interests=args.interests,
            travel_style=args.style, notes=args.notes,
        )
    except ValidationError as e:
        print(f"Invalid trip request:\n{e}", file=sys.stderr)
        return 2
    if req.start_date < date.today():
        print("Warning: the start date is in the past.", file=sys.stderr)

    orchestrator = TravelOrchestrator(web_search=not args.no_web_search, max_revisions=args.revisions)
    try:
        plan = await orchestrator.plan(req)
    except anthropic.AuthenticationError:
        print(
            "Claude API authentication failed. Set ANTHROPIC_API_KEY (see .env.example), "
            "or use --subscription to run on your Claude Pro/Max plan.",
            file=sys.stderr,
        )
        return 1
    except AgentError as e:
        print(f"Planning failed: {e}", file=sys.stderr)
        return 1

    slug = re.sub(r"[^a-z0-9]+", "-", plan.brief.destination_city.lower()).strip("-")
    out_dir = Path(args.out) / f"{slug}-{req.start_date}-{plan.generated_at:%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.html").write_text(render_html(plan), encoding="utf-8")
    (out_dir / "plan.md").write_text(render_markdown(plan), encoding="utf-8")
    (out_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    p = plan.pricing
    print(f"\nPlan ready: {out_dir.resolve() / 'plan.html'}")
    if p:
        status = "within budget" if p.within_budget else "OVER budget"
        print(f"Estimated total: {req.currency} {p.total:,.0f} of {req.currency} {req.budget:,.0f} ({status})")
    for w in plan.warnings:
        print(f"Warning: {w}")
    if args.open:
        webbrowser.open((out_dir / "plan.html").resolve().as_uri())
    return 0


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        for noisy in ("httpx", "anthropic"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
