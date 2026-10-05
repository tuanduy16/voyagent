"""Central orchestrator.

Flow:
  1. Brief      - orchestrator LLM resolves airports/currency and splits the budget.
  2. Fan-out    - flights, hotels and (weather -> activities) agents run concurrently.
  3. Pricing    - pricing agent selects options and costs the trip; totals are recomputed in code.
  4. Revision   - if over budget, the orchestrator reruns flights/hotels with caps (bounded loop).
  5. Synthesis  - orchestrator LLM writes the day-by-day itinerary from the selected options.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from . import config
from .agent import Agent
from .agents import specialists
from .models import (
    BudgetAllocation,
    FlightResult,
    HotelResult,
    Itinerary,
    PricingResult,
    TripBrief,
    TripPlan,
    TripRequest,
)
from .services import geo, http

log = logging.getLogger("voyagent")

BRIEF_SYSTEM = """You are the lead orchestrator of a multi-agent travel planner. Specialist agents for \
flights, hotels, weather, activities and pricing will work from your brief.

Write the brief:
- Resolve the origin and destination to cities and IATA codes (use a metro code such as NYC or LON where
  several airports serve the city). If the destination is a country or region, choose the best base city
  for the traveler's interests and say so in the guidance.
- Split the total budget across categories for ALL travelers in the traveler's currency. The split must
  sum to the total budget. Base it on realistic prices for this route, season and travel style.
- Give each specialist one or two sentences of specific guidance (e.g. neighborhoods to stay in,
  nonstop preference, must-see items for the stated interests).
- Be frank in budget_feasibility if the budget is unrealistic."""

ITINERARY_SYSTEM = """You are the lead orchestrator of a multi-agent travel planner. Your specialists have \
finished; write the final day-by-day itinerary from their results.

Rules:
- Exactly one DayPlan per calendar date from the trip start date through the end date, inclusive. Never add
  days outside that range: the outbound flight departs on the start date (if it is overnight, day 1 is the
  departure and the city starts on day 2).
- Day 1 starts with the selected outbound flight and hotel check-in; the last day ends with checkout and
  the return flight. Respect arrival times: don't schedule a full day if the flight lands in the evening.
- Use the selected activities; place outdoor ones on dry days and indoor ones on rainy days. Group
  activities by neighborhood to limit travel time. Leave some unscheduled time.
- Add meals (category 'food') using the food budget, naming real places from the shortlist where possible.
- Use the EXACT names of the selected flight, hotel and activities so booking links can be attached.
- `booking_query` is a short search phrase for the item (e.g. 'Belem Tower tickets Lisbon'); null for free
  time. `est_cost` is for all travelers in the traveler's currency (null if free or unknown).
- The booking checklist lists what to book first and how far ahead (flights, hotel, timed-entry tickets).
- Do not invent prices or facts beyond what the specialists reported."""


def _normalize_allocation(alloc: BudgetAllocation, budget: float) -> BudgetAllocation:
    total = alloc.total()
    if total <= 0:
        return alloc
    factor = budget / total
    return BudgetAllocation(**{k: round(v * factor, 2) for k, v in alloc.model_dump().items()})


def _finalize_pricing(
    pricing: PricingResult, req: TripRequest, flights: FlightResult | None, hotels: HotelResult | None
) -> PricingResult:
    """Never trust LLM arithmetic or indices: recompute and clamp them."""
    total = round(sum(li.amount for li in pricing.line_items), 2)
    pricing.total = total
    pricing.remaining = round(req.budget - total, 2)
    pricing.within_budget = total <= req.budget

    def clamp(index: int | None, result) -> int | None:
        if not result or not result.options:
            return None
        if index is None or not 0 <= index < len(result.options):
            return min(max(result.recommended_index, 0), len(result.options) - 1)
        return index

    pricing.selected_flight_index = clamp(pricing.selected_flight_index, flights)
    pricing.selected_hotel_index = clamp(pricing.selected_hotel_index, hotels)
    return pricing


class TravelOrchestrator:
    def __init__(self, web_search: bool = True, max_revisions: int = 1):
        self.web_search = web_search
        self.max_revisions = max_revisions
        self.warnings: list[str] = []

    async def _safe(self, label: str, coro):
        """Run a specialist; a failure degrades the plan instead of aborting it."""
        try:
            return await coro
        except Exception as e:
            log.error("[%s] failed: %s", label, e)
            self.warnings.append(f"The {label} agent failed ({e}); that section uses estimates or is missing.")
            return None

    async def _brief(self, req: TripRequest) -> TripBrief:
        agent = Agent(
            "orchestrator",
            BRIEF_SYSTEM,
            TripBrief,
            model=config.MODEL,
            effort=config.ORCHESTRATOR_EFFORT,
            max_turns=4,
        )
        task = req.model_dump_json(indent=1) + f"\nNights: {req.nights}\nToday's date: {datetime.now():%Y-%m-%d}"
        brief = await agent.run(task)
        brief.budget_allocation = _normalize_allocation(brief.budget_allocation, req.budget)
        return brief

    async def _weather_then_activities(self, req, brief, location):
        weather = None
        if location is None:
            self.warnings.append("Could not geocode the destination; weather and map lookups were skipped.")
        else:
            weather = await self._safe("weather", specialists.run_weather(req, location))
        activities = await self._safe(
            "activities", specialists.run_activities(req, brief, location, weather, self.web_search)
        )
        return weather, activities

    async def _itinerary(self, req, brief, weather, flights, hotels, activities, pricing) -> Itinerary:
        flight = flights.options[pricing.selected_flight_index] if flights and pricing and pricing.selected_flight_index is not None else None
        hotel = hotels.options[pricing.selected_hotel_index] if hotels and pricing and pricing.selected_hotel_index is not None else None
        chosen = activities.activities if activities else []
        if pricing and pricing.selected_activities:
            names = {n.lower() for n in pricing.selected_activities}
            chosen = [a for a in chosen if a.name.lower() in names] or chosen

        def dump(x) -> str:
            return x.model_dump_json(indent=1) if x is not None else "Not available."

        task = f"""Trip request:
{req.model_dump_json(indent=1)}

Brief:
{dump(brief)}

SELECTED FLIGHT:
{dump(flight)}

SELECTED HOTEL:
{dump(hotel)}

SELECTED ACTIVITIES:
{"[" + ",".join(a.model_dump_json() for a in chosen) + "]"}

Events during trip: {activities.events_during_trip if activities else []}

WEATHER:
{dump(weather)}

PRICING:
{dump(pricing)}"""
        agent = Agent(
            "itinerary",
            ITINERARY_SYSTEM,
            Itinerary,
            model=config.MODEL,
            effort=config.ORCHESTRATOR_EFFORT,
            max_turns=4,
            max_tokens=64000,
        )
        itinerary = await agent.run(task)
        in_range = [d for d in itinerary.days if req.start_date <= d.day <= req.end_date]
        if len(in_range) != len(itinerary.days):
            log.warning("dropping %d itinerary day(s) outside the trip dates", len(itinerary.days) - len(in_range))
            itinerary.days = in_range
        return itinerary

    async def plan(self, req: TripRequest) -> TripPlan:
        try:
            return await self._plan(req)
        finally:
            await http.aclose()

    async def _plan(self, req: TripRequest) -> TripPlan:
        log.info("== 1/5 Orchestrator brief")
        brief = await self._brief(req)
        location = None
        try:
            location = await geo.geocode(brief.destination_city, brief.destination_country)
        except Exception as e:
            log.warning("geocoding failed: %s", e)

        log.info("== 2/5 Specialists: flights, hotels, weather -> activities (parallel)")
        flights, hotels, (weather, activities) = await asyncio.gather(
            self._safe("flights", specialists.run_flights(req, brief, self.web_search)),
            self._safe("hotels", specialists.run_hotels(req, brief, self.web_search)),
            self._weather_then_activities(req, brief, location),
        )

        log.info("== 3/5 Pricing")
        pricing = await self._price(req, brief, flights, hotels, activities)

        revisions = 0
        while pricing and not pricing.within_budget and revisions < self.max_revisions:
            r = pricing.revision
            if not (r.rerun_flights or r.rerun_hotels):
                break
            revisions += 1
            over = pricing.total - req.budget
            log.info("== 4/5 Revision %d: over budget by %s %.0f", revisions, req.currency, over)
            note = f"\nREVISION: the previous plan was {req.currency} {over:,.0f} over budget. {r.guidance}"
            jobs = {}
            if r.rerun_flights:
                cap = f" Keep the round-trip total under {req.currency} {r.max_flight_total:,.0f}." if r.max_flight_total else ""
                jobs["flights"] = specialists.run_flights(req, brief, self.web_search, note + cap)
            if r.rerun_hotels:
                cap = f" Keep the nightly rate under {req.currency} {r.max_hotel_nightly:,.0f}." if r.max_hotel_nightly else ""
                jobs["hotels"] = specialists.run_hotels(req, brief, self.web_search, note + cap)
            results = await asyncio.gather(*(self._safe(k, v) for k, v in jobs.items()))
            updated = dict(zip(jobs, results))
            flights = updated.get("flights") or flights
            hotels = updated.get("hotels") or hotels
            pricing = await self._price(req, brief, flights, hotels, activities)

        if pricing and not pricing.within_budget:
            self.warnings.append(
                f"The plan is {req.currency} {pricing.total - req.budget:,.0f} over budget. See the savings tips."
            )

        log.info("== 5/5 Orchestrator writes the itinerary")
        itinerary = await self._itinerary(req, brief, weather, flights, hotels, activities, pricing)

        return TripPlan(
            request=req,
            brief=brief,
            location=location,
            weather=weather,
            flights=flights,
            hotels=hotels,
            activities=activities,
            pricing=pricing,
            itinerary=itinerary,
            revisions=revisions,
            warnings=self.warnings,
            generated_at=datetime.now(),
        )

    async def _price(self, req, brief, flights, hotels, activities) -> PricingResult | None:
        pricing = await self._safe(
            "pricing", specialists.run_pricing(req, brief, flights, hotels, activities, self.web_search)
        )
        return _finalize_pricing(pricing, req, flights, hotels) if pricing else None
