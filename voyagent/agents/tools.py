"""Tool definitions that expose the services to the agents."""

from __future__ import annotations

from ..agent import Tool
from ..services import currency, places, serpapi, weather


def convert_currency_tool() -> Tool:
    return Tool(
        name="convert_currency",
        description="Convert an amount between currencies using today's ECB reference rate.",
        input_schema={
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "from_currency": {"type": "string", "description": "ISO 4217 code, e.g. EUR"},
                "to_currency": {"type": "string", "description": "ISO 4217 code, e.g. USD"},
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
        handler=currency.convert_currency,
    )


def search_flights_tool() -> Tool:
    return Tool(
        name="search_flights",
        description=(
            "Live round-trip flight prices from Google Flights. Use IATA airport or metro codes. "
            "Returns options with price, airlines, stops and duration."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "origin_iata": {"type": "string"},
                "destination_iata": {"type": "string"},
                "outbound_date": {"type": "string", "description": "YYYY-MM-DD"},
                "return_date": {"type": "string", "description": "YYYY-MM-DD"},
                "adults": {"type": "integer"},
                "currency": {"type": "string"},
            },
            "required": ["origin_iata", "destination_iata", "outbound_date", "return_date", "adults", "currency"],
        },
        handler=serpapi.search_flights,
    )


def search_hotels_tool() -> Tool:
    return Tool(
        name="search_hotels",
        description=(
            "Live hotel prices from Google Hotels. `query` is free text such as 'hotels in Alfama, Lisbon'. "
            "Optionally cap the nightly price."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "check_in_date": {"type": "string", "description": "YYYY-MM-DD"},
                "check_out_date": {"type": "string", "description": "YYYY-MM-DD"},
                "adults": {"type": "integer"},
                "currency": {"type": "string"},
                "max_price_per_night": {"type": "number"},
            },
            "required": ["query", "check_in_date", "check_out_date", "adults", "currency"],
        },
        handler=serpapi.search_hotels,
    )


def weather_tool(latitude: float, longitude: float, start_date: str, end_date: str) -> Tool:
    async def handler() -> dict:
        return await weather.get_weather(latitude, longitude, start_date, end_date)

    return Tool(
        name="get_trip_weather",
        description=(
            "Daily weather for the destination over the trip dates: a live forecast when the dates are "
            "within ~2 weeks, otherwise typical conditions from previous years."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        prefetch=[{}],
    )


def find_places_tool(latitude: float, longitude: float) -> Tool:
    async def handler(interest: str) -> dict:
        return await places.find_places(latitude, longitude, interest)

    return Tool(
        name="find_places",
        description=(
            "Find real, named points of interest near the destination (Wikipedia, plus OpenStreetMap "
            "venues for food/nightlife/shopping) for one interest. "
            f"Supported interests: {', '.join(places.INTEREST_KEYWORDS)} (others map to 'sightseeing'). "
            "Most notable places come first."
        ),
        input_schema={
            "type": "object",
            "properties": {"interest": {"type": "string"}},
            "required": ["interest"],
        },
        handler=handler,
    )
