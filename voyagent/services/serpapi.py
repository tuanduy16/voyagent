"""Optional live prices from Google Flights / Google Hotels via SerpApi.

Only used when SERPAPI_API_KEY is set. Responses are trimmed to the fields the
agents need so they don't flood the context window.
"""

from __future__ import annotations

from .. import config
from .http import client

SERPAPI_URL = "https://serpapi.com/search.json"


async def _search(params: dict) -> dict:
    resp = await client().get(SERPAPI_URL, params={**params, "api_key": config.SERPAPI_API_KEY, "hl": "en"})
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


async def search_flights(
    origin_iata: str, destination_iata: str, outbound_date: str, return_date: str,
    adults: int, currency: str, max_results: int = 8,
) -> dict:
    data = await _search(
        {
            "engine": "google_flights",
            "departure_id": origin_iata, "arrival_id": destination_iata,
            "outbound_date": outbound_date, "return_date": return_date,
            "adults": adults, "currency": currency, "type": 1,
        }
    )
    options = []
    for group in ("best_flights", "other_flights"):
        for item in data.get(group, []):
            legs = item.get("flights", [])
            if not legs:
                continue
            options.append(
                {
                    "group": group,
                    "price": item.get("price"),
                    "airlines": sorted({leg.get("airline", "") for leg in legs}),
                    "departure": f"{legs[0]['departure_airport'].get('id')} {legs[0]['departure_airport'].get('time')}",
                    "arrival": f"{legs[-1]['arrival_airport'].get('id')} {legs[-1]['arrival_airport'].get('time')}",
                    "stops": len(legs) - 1,
                    "layovers": [l.get("name") for l in item.get("layovers", [])],
                    "total_duration_min": item.get("total_duration"),
                    "flight_numbers": [leg.get("flight_number") for leg in legs],
                    "travel_class": legs[0].get("travel_class"),
                }
            )
    return {
        "source": "Google Flights via SerpApi",
        "note": (
            f"Prices are round-trip in {currency} as Google Flights reports them for {adults} adult(s); "
            "details shown are the outbound leg (the return is chosen at booking)."
        ),
        "price_insights": data.get("price_insights"),
        "google_flights_url": data.get("search_metadata", {}).get("google_flights_url"),
        "options": options[:max_results],
    }


async def search_hotels(
    query: str, check_in_date: str, check_out_date: str, adults: int, currency: str,
    max_price_per_night: float | None = None, max_results: int = 10,
) -> dict:
    params = {
        "engine": "google_hotels", "q": query,
        "check_in_date": check_in_date, "check_out_date": check_out_date,
        "adults": adults, "currency": currency, "sort_by": 3 if max_price_per_night else None,
    }
    if max_price_per_night:
        params["max_price"] = int(max_price_per_night)
    data = await _search({k: v for k, v in params.items() if v is not None})
    hotels = []
    for p in data.get("properties", []):
        hotels.append(
            {
                "name": p.get("name"),
                "type": p.get("type"),
                "nightly_rate": (p.get("rate_per_night") or {}).get("extracted_lowest"),
                "total_rate": (p.get("total_rate") or {}).get("extracted_lowest"),
                "rating": p.get("overall_rating"),
                "reviews": p.get("reviews"),
                "hotel_class": p.get("extracted_hotel_class"),
                "amenities": (p.get("amenities") or [])[:8],
                "link": p.get("link"),
            }
        )
    return {
        "source": "Google Hotels via SerpApi",
        "note": f"Rates in {currency} for {adults} adult(s).",
        "hotels": hotels[:max_results],
    }
