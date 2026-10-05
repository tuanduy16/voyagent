"""City geocoding via Open-Meteo (free, no key)."""

from .http import client

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


async def geocode(place: str, country_hint: str = "") -> dict | None:
    name = place.split(",")[0].strip()
    hint = (country_hint or place[len(name):]).strip(" ,").lower()
    resp = await client().get(GEOCODE_URL, params={"name": name, "count": 10, "language": "en", "format": "json"})
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    best = results[0]
    if hint:
        for r in results:
            where = f"{r.get('country', '')} {r.get('country_code', '')} {r.get('admin1', '')}".lower()
            if hint in where or any(part and part in where for part in hint.split()):
                best = r
                break
    return {
        "name": best["name"],
        "country": best.get("country"),
        "country_code": best.get("country_code"),
        "latitude": best["latitude"],
        "longitude": best["longitude"],
        "timezone": best.get("timezone"),
    }
