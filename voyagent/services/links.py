"""Deterministic booking/search deep links.

Links are built in code from structured fields (never written by the LLM), so
they always point at a real search page pre-filled with the trip details.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote, quote_plus, urlencode

Link = dict[str, str]


def flight_links(origin: str, destination: str, start: date, end: date, travelers: int) -> list[Link]:
    o, d = origin.upper(), destination.upper()
    gf_query = f"Flights from {o} to {d} on {start.isoformat()} through {end.isoformat()}"
    kayak_pax = f"/{travelers}adults" if travelers > 1 else ""
    return [
        {"label": "Google Flights", "url": f"https://www.google.com/travel/flights?q={quote_plus(gf_query)}"},
        {
            "label": "Skyscanner",
            "url": f"https://www.skyscanner.com/transport/flights/{o.lower()}/{d.lower()}/"
            f"{start:%y%m%d}/{end:%y%m%d}/?adultsv2={travelers}",
        },
        {"label": "Kayak", "url": f"https://www.kayak.com/flights/{o}-{d}/{start.isoformat()}/{end.isoformat()}{kayak_pax}"},
    ]


def hotel_links(city: str, start: date, end: date, travelers: int, hotel_name: str | None = None) -> list[Link]:
    target = f"{hotel_name}, {city}" if hotel_name else city
    booking = urlencode(
        {"ss": target, "checkin": start.isoformat(), "checkout": end.isoformat(), "group_adults": travelers, "no_rooms": 1}
    )
    expedia = urlencode(
        {"destination": target, "startDate": start.isoformat(), "endDate": end.isoformat(), "adults": travelers}
    )
    links = [
        {"label": "Booking.com", "url": f"https://www.booking.com/searchresults.html?{booking}"},
        {"label": "Expedia", "url": f"https://www.expedia.com/Hotel-Search?{expedia}"},
        {"label": "Google Hotels", "url": f"https://www.google.com/travel/search?q={quote_plus(target)}"},
    ]
    if not hotel_name:
        airbnb = urlencode({"checkin": start.isoformat(), "checkout": end.isoformat(), "adults": travelers})
        links.append({"label": "Airbnb", "url": f"https://www.airbnb.com/s/{quote(city)}/homes?{airbnb}"})
    return links


def activity_links(query: str, city: str) -> list[Link]:
    q = query if city.lower() in query.lower() else f"{query} {city}"
    return [
        {"label": "GetYourGuide", "url": f"https://www.getyourguide.com/s/?q={quote_plus(q)}"},
        {"label": "Viator", "url": f"https://www.viator.com/searchResults/all?text={quote_plus(q)}"},
        maps_link(q),
    ]


def maps_link(query: str) -> Link:
    return {"label": "Map", "url": f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"}
