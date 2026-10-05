"""The five specialist agents: flights, hotels, weather, activities, pricing.

Each `run_*` function builds a focused agent with only the tools it needs and
returns a validated Pydantic result.
"""

from __future__ import annotations

from .. import config
from ..agent import Agent
from ..models import (
    ActivitiesResult,
    FlightResult,
    HotelResult,
    PricingResult,
    TripBrief,
    TripRequest,
    WeatherResult,
)
from .tools import (
    convert_currency_tool,
    find_places_tool,
    search_flights_tool,
    search_hotels_tool,
    weather_tool,
)

HONESTY_RULES = """\
Ground rules:
- Prices must come from a tool result or a web search result. If you cannot find a real price, give a
  realistic estimate and mark it as an estimate. Never present an estimate as a quote.
- All money amounts you submit are in the traveler's currency ({currency}). Use convert_currency when a
  source quotes another currency.
- `source_url` may only be a URL that appeared in a tool or web search result; otherwise use null.
- Be efficient: a few well-chosen searches, then submit."""


def _trip_header(req: TripRequest, brief: TripBrief) -> str:
    return (
        f"Trip: {brief.origin_city} ({brief.origin_airport_iata}) -> {brief.destination_city}, "
        f"{brief.destination_country} ({brief.destination_airport_iata})\n"
        f"Dates: {req.start_date} to {req.end_date} ({req.nights} nights)\n"
        f"Travelers: {req.travelers} | Style: {req.travel_style} | Currency: {req.currency}\n"
        f"Total budget: {req.currency} {req.budget:,.0f}\n"
        f"Interests: {', '.join(req.interests) or 'general sightseeing'}\n"
        f"Traveler notes: {req.notes or 'none'}"
    )


def _fx_tool(req: TripRequest, brief: TripBrief):
    """convert_currency, prefetching the local->traveler rate for subscription mode."""
    tool = convert_currency_tool()
    if brief.destination_currency.upper() != req.currency:
        tool.prefetch = [{"amount": 1, "from_currency": brief.destination_currency, "to_currency": req.currency}]
    return tool


def _source_hint(live_tool: str, web_search: bool) -> str:
    if config.SERPAPI_API_KEY:
        return f"Use the {live_tool} tool first (live prices); set data_source='live_api'."
    if web_search:
        return "No live price API is configured. Use web search for current prices; set data_source='web_search'."
    return "No live price source is available. Give realistic estimates; set data_source='estimate'."


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------

FLIGHTS_SYSTEM = """You are the Flights specialist in a multi-agent travel planner. Find 2-4 good round-trip \
flight options for the trip and recommend one, balancing price, total travel time, stops and arrival \
times. Prefer options that fit the flight budget. `price_total` is the round-trip price for ALL travelers.
The outbound flight must DEPART on the trip start date and the return must DEPART on the end date (an
overnight outbound flight lands the next day; that is fine). Never move the dates.

""" + HONESTY_RULES


async def run_flights(req: TripRequest, brief: TripBrief, web_search: bool, constraints: str = "") -> FlightResult:
    tools = [_fx_tool(req, brief)]
    if config.SERPAPI_API_KEY:
        flights_tool = search_flights_tool()
        flights_tool.prefetch = [{
            "origin_iata": brief.origin_airport_iata, "destination_iata": brief.destination_airport_iata,
            "outbound_date": req.start_date.isoformat(), "return_date": req.end_date.isoformat(),
            "adults": req.travelers, "currency": req.currency,
        }]
        tools.append(flights_tool)
    agent = Agent(
        "flights",
        FLIGHTS_SYSTEM.format(currency=req.currency),
        FlightResult,
        tools,
        web_search=web_search and not config.SERPAPI_API_KEY,
        max_web_searches=4,
    )
    task = f"""{_trip_header(req, brief)}

Flight budget (all travelers): {req.currency} {brief.budget_allocation.flights:,.0f}
Orchestrator guidance: {brief.flight_guidance}
{_source_hint("search_flights", web_search)}
{constraints}"""
    return await agent.run(task)


# ---------------------------------------------------------------------------
# Hotels
# ---------------------------------------------------------------------------

HOTELS_SYSTEM = """You are the Hotels specialist in a multi-agent travel planner. Find 3-5 places to stay \
for the whole trip and recommend one. Consider location (walkability to the traveler's interests, \
safety, transit), guest ratings, and value. Rates are for the whole party (one room, or an apartment \
for larger groups).

""" + HONESTY_RULES


async def run_hotels(req: TripRequest, brief: TripBrief, web_search: bool, constraints: str = "") -> HotelResult:
    tools = [_fx_tool(req, brief)]
    if config.SERPAPI_API_KEY:
        hotels_tool = search_hotels_tool()
        hotels_tool.prefetch = [{
            "query": f"hotels in {brief.destination_city}",
            "check_in_date": req.start_date.isoformat(), "check_out_date": req.end_date.isoformat(),
            "adults": req.travelers, "currency": req.currency,
        }]
        tools.append(hotels_tool)
    agent = Agent(
        "hotels",
        HOTELS_SYSTEM.format(currency=req.currency),
        HotelResult,
        tools,
        web_search=web_search and not config.SERPAPI_API_KEY,
        max_web_searches=4,
    )
    nightly = brief.budget_allocation.lodging / max(req.nights, 1)
    task = f"""{_trip_header(req, brief)}

Lodging budget: {req.currency} {brief.budget_allocation.lodging:,.0f} total (~{req.currency} {nightly:,.0f}/night)
Orchestrator guidance: {brief.hotel_guidance}
{_source_hint("search_hotels", web_search)}
{constraints}"""
    return await agent.run(task)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

WEATHER_SYSTEM = """You are the Weather specialist in a multi-agent travel planner. Call get_trip_weather, \
then summarize conditions for the trip. Be explicit about whether the data is a live forecast or typical \
historical conditions. Flag days likely to be rainy (rain chance >= 50% or rain in the condition) so the \
activities planner can schedule indoor plans, and give practical packing tips."""


async def run_weather(req: TripRequest, location: dict) -> WeatherResult:
    agent = Agent(
        "weather",
        WEATHER_SYSTEM,
        WeatherResult,
        [weather_tool(location["latitude"], location["longitude"], req.start_date.isoformat(), req.end_date.isoformat())],
        effort="low",
        max_turns=6,
    )
    task = (
        f"Destination: {location['name']}, {location.get('country')}\n"
        f"Dates: {req.start_date} to {req.end_date}\n"
        f"Interests: {', '.join(req.interests) or 'general sightseeing'}"
    )
    return await agent.run(task)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

ACTIVITIES_SYSTEM = """You are the Activities specialist in a multi-agent travel planner. Build a shortlist \
of 10-18 activities, sights and food experiences that match the traveler's interests. Use find_places \
for real, named places (one call per interest) and web search for ticket prices, opening days and events \
happening during the trip. Prefer outdoor plans on dry days and indoor plans on rainy days, and suggest \
a day for each item when the weather makes it matter. Include a mix of free and paid items that fits \
the activities budget. `est_cost_per_person` is per person.

""" + HONESTY_RULES


async def run_activities(
    req: TripRequest, brief: TripBrief, location: dict | None, weather: WeatherResult | None, web_search: bool
) -> ActivitiesResult:
    tools = [_fx_tool(req, brief)]
    if location:
        places_tool = find_places_tool(location["latitude"], location["longitude"])
        interests = list(dict.fromkeys([*(req.interests or ["sightseeing"]), "food"]))
        places_tool.prefetch = [{"interest": i} for i in interests]
        tools.append(places_tool)
    agent = Agent(
        "activities",
        ACTIVITIES_SYSTEM.format(currency=req.currency),
        ActivitiesResult,
        tools,
        web_search=web_search,
        max_web_searches=5,
    )
    weather_text = weather.model_dump_json(indent=1) if weather else "Weather data unavailable."
    task = f"""{_trip_header(req, brief)}

Activities budget (all travelers): {req.currency} {brief.budget_allocation.activities:,.0f}
Orchestrator guidance: {brief.activity_guidance}

Weather report:
{weather_text}"""
    return await agent.run(task)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

PRICING_SYSTEM = """You are the Pricing specialist in a multi-agent travel planner. You receive the options \
found by the flights, hotels and activities agents. Your job:
1. Pick the flight, hotel and set of activities that give the best trip within the total budget.
   Selected indices are 0-based positions in the option lists.
2. Estimate food (per day, all travelers, matching the travel style and local prices) and local transport
   (airport transfers + getting around). Use web search sparingly if you need local price levels.
3. Produce line items covering every cost, all in the traveler's currency. Keep a small contingency
   line ('other') if the budget allows.
4. If the trip cannot fit the budget even with the cheapest options listed, set the revision fields:
   which searches should be rerun and with what caps. Otherwise set rerun_* to false.
The orchestrator recomputes the total from your line items, so make the line items complete.

""" + HONESTY_RULES


async def run_pricing(
    req: TripRequest,
    brief: TripBrief,
    flights: FlightResult | None,
    hotels: HotelResult | None,
    activities: ActivitiesResult | None,
    web_search: bool,
) -> PricingResult:
    agent = Agent(
        "pricing",
        PRICING_SYSTEM.format(currency=req.currency),
        PricingResult,
        [_fx_tool(req, brief)],
        web_search=web_search,
        max_web_searches=2,
    )

    def dump(result) -> str:
        return result.model_dump_json(indent=1) if result else "Not available (agent failed) - estimate this category."

    task = f"""{_trip_header(req, brief)}
Local currency: {brief.destination_currency}
Orchestrator's budget allocation: {brief.budget_allocation.model_dump_json()}

FLIGHT OPTIONS:
{dump(flights)}

HOTEL OPTIONS:
{dump(hotels)}

ACTIVITY SHORTLIST:
{dump(activities)}"""
    return await agent.run(task)
