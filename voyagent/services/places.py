"""Real, named points of interest near the destination (free, no key).

Primary source: Wikipedia GeoSearch (fast and reliable; notable sights, museums,
parks, neighborhoods), ranked by article length as a notability proxy.
For food / nightlife / shopping we also try OpenStreetMap's Overpass API, which
knows individual restaurants and bars but is often overloaded, so it is best-effort.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from .http import client

WIKI_API = "https://en.wikipedia.org/w/api.php"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "museums": ("museum",),
    "history": ("castle", "palace", "monastery", "fort", "ruin", "monument", "historic", "cathedral",
                "archaeological", "aqueduct", "memorial", "convent", "old town", "tower"),
    "art": ("art", "gallery", "museum of", "street art"),
    "food": ("market", "food hall", "restaurant", "café", "cafe", "bakery", "pastry", "brewery", "winery"),
    "nightlife": ("bar", "nightclub", "music venue", "jazz", "club", "theatre", "concert", "live music"),
    "nature": ("park", "garden", "beach", "nature reserve", "forest", "botanical", "mountain", "lake",
               "river", "island", "waterfall"),
    "shopping": ("market", "shopping", "mall", "street", "avenue", "department store", "arcade"),
    "family": ("zoo", "aquarium", "theme park", "science", "amusement", "children", "planetarium"),
    "views": ("viewpoint", "lookout", "tower", "hill", "observation", "bridge", "skyscraper"),
    "architecture": ("cathedral", "church", "basilica", "palace", "building", "tower", "bridge", "station",
                     "architect", "mosque", "temple"),
}
INTEREST_KEYWORDS["sightseeing"] = tuple(
    sorted({kw for k in ("museums", "history", "nature", "views", "architecture") for kw in INTEREST_KEYWORDS[k]})
    + ["square", "neighbourhood", "neighborhood", "district", "landmark"]
)

OVERPASS_FILTERS = {
    "food": ['node["amenity"="restaurant"]["cuisine"]["website"]', 'nw["amenity"="marketplace"]'],
    "nightlife": ['node["amenity"~"^(bar|nightclub|pub)$"]["website"]'],
    "shopping": ['nw["amenity"="marketplace"]', 'nw["shop"~"^(mall|department_store)$"]'],
}

ALIASES = {
    "museum": "museums", "historical": "history", "historic": "history", "culture": "history",
    "galleries": "art", "culinary": "food", "restaurants": "food", "foodie": "food", "cuisine": "food",
    "bars": "nightlife", "clubs": "nightlife", "music": "nightlife", "outdoors": "nature", "hiking": "nature",
    "parks": "nature", "beach": "nature", "beaches": "nature", "kids": "family", "family-friendly": "family",
    "photography": "views", "landmarks": "sightseeing", "attractions": "sightseeing",
}

_wiki_cache: dict[tuple[float, float], list[dict]] = {}


def normalize_interest(interest: str) -> str:
    key = interest.strip().lower()
    key = ALIASES.get(key, key)
    return key if key in INTEREST_KEYWORDS else "sightseeing"


def _maps_url(name: str, lat=None, lon=None) -> str:
    query = f"{lat},{lon}" if lat is not None and lon is not None else name
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


async def _wikipedia_nearby(lat: float, lon: float) -> list[dict]:
    key = (round(lat, 3), round(lon, 3))
    if key not in _wiki_cache:
        resp = await client().get(
            WIKI_API,
            params={
                "action": "query", "format": "json", "formatversion": 2,
                "generator": "geosearch", "ggscoord": f"{lat}|{lon}", "ggsradius": 10000, "ggslimit": 500,
                "prop": "description|info|coordinates", "inprop": "url",
            },
        )
        resp.raise_for_status()
        _wiki_cache[key] = resp.json().get("query", {}).get("pages", [])
    return _wiki_cache[key]


async def _overpass(lat: float, lon: float, category: str) -> list[dict]:
    radius = 3000
    body = "".join(f'{f}["name"](around:{radius},{lat},{lon});' for f in OVERPASS_FILTERS[category])
    query = f"[out:json][timeout:15];({body});out center tags 150;"
    resp = await client().post(OVERPASS_URL, data={"data": query}, timeout=25)
    resp.raise_for_status()
    data = resp.json()
    if data.get("remark", "").startswith("runtime error"):
        raise RuntimeError(data["remark"])
    out, seen = [], set()
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        plat = el.get("lat") or el.get("center", {}).get("lat")
        plon = el.get("lon") or el.get("center", {}).get("lon")
        out.append(
            {
                "name": name,
                "type": tags.get("amenity") or tags.get("shop") or "",
                "cuisine": tags.get("cuisine"),
                "opening_hours": tags.get("opening_hours"),
                "website": tags.get("website") or tags.get("contact:website"),
                "maps_url": _maps_url(name, plat, plon),
                "source": "OpenStreetMap",
            }
        )
    return out[:20]


async def find_places(latitude: float, longitude: float, interest: str, limit: int = 20) -> dict:
    category = normalize_interest(interest)
    keywords = INTEREST_KEYWORDS[category]
    want_osm = category in OVERPASS_FILTERS

    wiki_task = _wikipedia_nearby(latitude, longitude)
    osm_task = _overpass(latitude, longitude, category) if want_osm else asyncio.sleep(0, result=[])
    wiki_pages, osm_places = await asyncio.gather(wiki_task, osm_task, return_exceptions=True)

    errors = []
    places: list[dict] = []
    if isinstance(osm_places, Exception):
        errors.append(
            f"OpenStreetMap unavailable ({type(osm_places).__name__}); use web search for specific venues."
        )
    else:
        places.extend(osm_places)

    if isinstance(wiki_pages, Exception):
        errors.append(f"Wikipedia unavailable ({type(wiki_pages).__name__}: {wiki_pages}).")
    else:
        matches = [
            p for p in wiki_pages
            if any(kw in f"{p.get('description', '')} {p['title']}".lower() for kw in keywords)
        ]
        matches.sort(key=lambda p: p.get("length", 0), reverse=True)  # longer article ~ more notable
        for p in matches[: limit]:
            coords = (p.get("coordinates") or [{}])[0]
            places.append(
                {
                    "name": p["title"],
                    "description": p.get("description"),
                    "wikipedia_url": p.get("fullurl"),
                    "maps_url": _maps_url(p["title"], coords.get("lat"), coords.get("lon")),
                    "source": "Wikipedia",
                }
            )

    result = {"interest": category, "places": places}
    if errors:
        result["warnings"] = errors
    if not places:
        result["hint"] = "No places found from the free map sources; use web search instead."
    return result
