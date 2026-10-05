"""Smoke-test the free data services and schemas (no Claude API key needed).

    python tests/smoke_services.py
"""

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voyagent.agent import inline_schema  # noqa: E402
from voyagent.models import ActivitiesResult, Itinerary, PricingResult, TripBrief  # noqa: E402
from voyagent.services import currency, geo, http, places, weather  # noqa: E402


async def main() -> None:
    for model in (TripBrief, ActivitiesResult, PricingResult, Itinerary):
        schema = json.dumps(inline_schema(model))
        assert "$ref" not in schema, model
    print("schemas: ok")

    loc = await geo.geocode("Lisbon", "Portugal")
    print("geocode:", loc)

    soon = date.today() + timedelta(days=3)
    w = await weather.get_weather(loc["latitude"], loc["longitude"], soon.isoformat(), (soon + timedelta(days=2)).isoformat())
    print("forecast:", w["mode"], w["days"][0])

    later = date.today() + timedelta(days=60)
    w = await weather.get_weather(loc["latitude"], loc["longitude"], later.isoformat(), (later + timedelta(days=2)).isoformat())
    print("typical:", w["mode"], w["days"][0])

    for interest in ("museum", "history", "food"):
        p = await places.find_places(loc["latitude"], loc["longitude"], interest)
        print(f"places[{interest}]:", [x["name"] for x in p["places"][:6]], p.get("warnings", ""))

    print("currency:", await currency.convert_currency(100, "USD", "EUR"))
    await http.aclose()


asyncio.run(main())
