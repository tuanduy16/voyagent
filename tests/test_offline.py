"""Offline tests: no Claude API calls, no network.

    python tests/test_offline.py        (or: pytest tests/test_offline.py)

1. The agent loop, driven by scripted fake Claude responses.
2. The orchestrator end to end, with each agent's output canned, including an
   over-budget revision round, then HTML/Markdown rendering.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voyagent import agent as agent_mod  # noqa: E402
from voyagent import orchestrator as orch_mod  # noqa: E402
from voyagent.agent import Agent, Tool  # noqa: E402
from voyagent.models import (  # noqa: E402
    ActivitiesResult, FlightResult, HotelResult, Itinerary, PricingResult, TripBrief, TripRequest, WeatherResult,
)
from voyagent.render import render_html, render_markdown  # noqa: E402

START = date.today() + timedelta(days=40)
END = START + timedelta(days=4)


# ---------------------------------------------------------------------------
# 1. Agent loop
# ---------------------------------------------------------------------------


def _msg(stop_reason: str, *blocks) -> NS:
    return NS(stop_reason=stop_reason, content=list(blocks), stop_details=None)


def _tool_use(id_: str, name: str, inp: dict) -> NS:
    return NS(type="tool_use", id=id_, name=name, input=inp)


def test_agent_loop() -> None:
    calls = []

    async def lookup(city: str) -> dict:
        calls.append(city)
        return {"price": 120, "url": "https://real.example.com/deal"}

    tool = Tool("lookup", "test tool", {"type": "object", "properties": {"city": {"type": "string"}}}, lookup)
    good_hotel = {
        "name": "Hotel A", "neighborhood": "Center", "nightly_rate": 120, "total_price": 480,
        "price_is_estimate": False, "rating": 4.5, "stars": 4, "why": "central",
        "source_url": "https://real.example.com/deal",
    }
    invented = {**good_hotel, "name": "Hotel B", "source_url": "https://made-up.example.com/x"}
    script = iter(
        [
            _msg("end_turn", NS(type="text", text="thinking out loud")),           # no tool call -> nudge
            _msg("tool_use", _tool_use("t1", "lookup", {"city": "Lisbon"})),      # real tool
            _msg("tool_use", _tool_use("t2", "submit_result", {"options": []})),  # invalid -> error back
            _msg("tool_use", _tool_use("t3", "submit_result", {
                "options": [good_hotel, invented], "recommended_index": 0,
                "data_source": "live_api", "summary": "ok",
            })),
        ]
    )
    sent: list[list] = []

    async def fake_call(self, messages):
        sent.append(list(messages))
        return next(script)

    original = Agent._call
    Agent._call = fake_call
    try:
        result = asyncio.run(Agent("hotels", "sys", HotelResult, [tool]).run("find hotels"))
    finally:
        Agent._call = original

    assert calls == ["Lisbon"]
    assert len(sent) == 4
    assert "submit_result" in sent[1][-1]["content"]  # the nudge
    validation_reply = sent[3][-1]["content"][0]
    assert validation_reply["is_error"] and "Validation failed" in validation_reply["content"]
    assert result.options[0].source_url == "https://real.example.com/deal"  # seen in a tool result -> kept
    assert result.options[1].source_url is None  # never seen -> scrubbed
    print("agent loop: ok")


# ---------------------------------------------------------------------------
# 2. Orchestrator end to end with canned agent outputs
# ---------------------------------------------------------------------------

REQ = TripRequest(
    origin="Boston, USA", destination="Lisbon, Portugal", start_date=START, end_date=END,
    budget=3000, travelers=2, interests=["food", "history"],
)

BRIEF = TripBrief(
    origin_city="Boston", origin_airport_iata="BOS", destination_city="Lisbon", destination_country="Portugal",
    destination_airport_iata="LIS", destination_currency="EUR",
    budget_allocation=dict(flights=1200, lodging=800, activities=300, food=400, local_transport=100, buffer=100),
    budget_feasibility="Tight but doable.", flight_guidance="Nonstop TAP.", hotel_guidance="Baixa/Chiado.",
    activity_guidance="Belem + Alfama.",
)


def _flights(price: float) -> FlightResult:
    return FlightResult(
        options=[dict(airline="TAP Air Portugal", outbound_summary="BOS 21:35 -> LIS 09:05+1, nonstop",
                      return_summary="LIS 11:00 -> BOS 13:40, nonstop", stops=0, price_total=price,
                      price_is_estimate=False, cabin="Economy", source_url=None, notes="")],
        recommended_index=0, data_source="live_api", summary="TAP nonstop is best.",
    )


def _hotels(nightly: float) -> HotelResult:
    return HotelResult(
        options=[dict(name="Lisboa Pessoa Hotel", neighborhood="Chiado", nightly_rate=nightly,
                      total_price=nightly * REQ.nights, price_is_estimate=True, rating=4.6, stars=4,
                      why="Walkable to everything", source_url=None)],
        recommended_index=0, data_source="web_search", summary="Chiado is central.",
    )


WEATHER = WeatherResult(
    data_source="historical_typical", summary="Mild, some showers.",
    daily=[dict(day=START + timedelta(days=i), condition="Typically partly cloudy", temp_high_c=19,
                temp_low_c=12, rain_chance_pct=30) for i in range(REQ.nights + 1)],
    rainy_days=[START + timedelta(days=2)], packing_tips=["Light rain jacket"], activity_advice="Museums on day 3.",
)

ACTIVITIES = ActivitiesResult(
    activities=[
        dict(name="Jerónimos Monastery", category="landmark", description="Manueline masterpiece",
             neighborhood="Belém", est_cost_per_person=18, duration_hours=2, indoor=True, requires_booking=True,
             best_time="morning", suggested_day=None, booking_query="Jeronimos Monastery tickets", source_url=None),
        dict(name="Time Out Market", category="food", description="Food hall", neighborhood="Cais do Sodré",
             est_cost_per_person=25, duration_hours=1.5, indoor=True, requires_booking=False, best_time="lunch",
             suggested_day=None, booking_query="Time Out Market Lisboa", source_url=None),
    ],
    events_during_trip=[], summary="History + food.",
)


def _pricing(flight_total: float, hotel_total: float, rerun: bool) -> PricingResult:
    items = [
        dict(category="flights", description="TAP", amount=flight_total),
        dict(category="lodging", description="Hotel", amount=hotel_total),
        dict(category="activities", description="Tickets", amount=86),
        dict(category="food", description="Meals", amount=400),
    ]
    return PricingResult(
        selected_flight_index=7,  # deliberately out of range: orchestrator must clamp it
        selected_hotel_index=0, selected_activities=["Jerónimos Monastery", "Time Out Market"],
        line_items=items, total=1.0, remaining=0, within_budget=True,  # deliberately wrong: must be recomputed
        currency_notes="1 USD = 0.89 EUR", savings_tips=["Lisboa Card"],
        revision=dict(rerun_flights=rerun, rerun_hotels=rerun, max_flight_total=1300 if rerun else None,
                      max_hotel_nightly=150 if rerun else None, guidance="Cheaper options"),
    )


ITINERARY = Itinerary(
    title="Lisbon: History & Food", overview="Five days of tiles, tarts and trams.",
    days=[dict(day=START + timedelta(days=i), title=f"Day {i + 1}", weather_note="Mild",
               items=[dict(time="10:00", title="Jerónimos Monastery", category="activity", description="Visit",
                           est_cost=36, booking_query="Jeronimos Monastery tickets"),
                      dict(time="13:00", title="Lunch at Time Out Market", category="food", description="Eat",
                           est_cost=50, booking_query="Time Out Market Lisboa")])
          for i in range(REQ.nights + 1)],
    booking_checklist=["Book flights now"], packing_list=["Comfortable shoes"], local_tips=["Validate tram tickets"],
)


def test_orchestrator_end_to_end() -> None:
    runs: list[str] = []
    pricing_rounds = iter([_pricing(2100, 1200, rerun=True), _pricing(1250, 700, rerun=False)])
    flight_rounds = iter([_flights(2100), _flights(1250)])
    hotel_rounds = iter([_hotels(300), _hotels(175)])

    async def fake_run(self, task: str):
        runs.append(self.name)
        if self.name == "flights" and runs.count("flights") == 2:
            assert "REVISION" in task and "1,300" in task
        return {
            "orchestrator": lambda: BRIEF.model_copy(deep=True),
            "flights": lambda: next(flight_rounds),
            "hotels": lambda: next(hotel_rounds),
            "weather": lambda: WEATHER,
            "activities": lambda: ACTIVITIES,
            "pricing": lambda: next(pricing_rounds),
            "itinerary": lambda: ITINERARY,
        }[self.name]()

    async def fake_geocode(city, country=""):
        return {"name": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1}

    original_run, original_geo = Agent.run, orch_mod.geo.geocode
    Agent.run, orch_mod.geo.geocode = fake_run, fake_geocode
    try:
        plan = asyncio.run(orch_mod.TravelOrchestrator(web_search=False, max_revisions=1).plan(REQ))
    finally:
        Agent.run, orch_mod.geo.geocode = original_run, original_geo

    assert runs[0] == "orchestrator" and runs[-1] == "itinerary"
    assert runs.count("flights") == 2 and runs.count("hotels") == 2 and runs.count("pricing") == 2
    assert plan.revisions == 1
    assert plan.pricing.total == 1250 + 700 + 86 + 400  # recomputed from line items
    assert plan.pricing.within_budget
    assert plan.pricing.selected_flight_index == 0  # clamped
    assert abs(plan.brief.budget_allocation.total() - REQ.budget) < 0.05  # normalized

    html, md = render_html(plan), render_markdown(plan)
    for needle in ("skyscanner.com/transport/flights/bos/lis", "booking.com/searchresults.html",
                   "getyourguide.com", "Lisboa Pessoa Hotel", "Budget breakdown"):
        assert needle in html, needle
    assert "Day 5" in md

    out = Path(__file__).resolve().parents[1] / "output" / "sample"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.html").write_text(html, encoding="utf-8")
    (out / "plan.md").write_text(md, encoding="utf-8")
    print(f"orchestrator end-to-end: ok  (sample written to {out / 'plan.html'})")


if __name__ == "__main__":
    test_agent_loop()
    test_orchestrator_end_to_end()
