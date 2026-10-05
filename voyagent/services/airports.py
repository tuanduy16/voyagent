"""Nearest commercial airport from the free OurAirports dataset (downloaded once, then cached)."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from .http import client

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
CACHE_FILE = Path(__file__).resolve().parents[2] / ".cache" / "airports.csv"

_airports: list[dict] | None = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


async def _load() -> list[dict]:
    global _airports
    if _airports is not None:
        return _airports
    if not CACHE_FILE.exists():
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        resp = await client().get(AIRPORTS_URL, timeout=120)
        resp.raise_for_status()
        CACHE_FILE.write_bytes(resp.content)
    with CACHE_FILE.open(encoding="utf-8") as f:
        _airports = [
            {
                "iata": row["iata_code"],
                "name": row["name"],
                "type": row["type"],
                "lat": float(row["latitude_deg"]),
                "lon": float(row["longitude_deg"]),
            }
            for row in csv.DictReader(f)
            if row["iata_code"]
            and row["scheduled_service"] == "yes"
            and row["type"] in ("large_airport", "medium_airport")
        ]
    return _airports


async def nearest_airport(lat: float, lon: float, max_km: float = 150) -> dict | None:
    airports = await _load()
    ranked = sorted(airports, key=lambda a: haversine_km(lat, lon, a["lat"], a["lon"]))
    for wanted in ("large_airport", "medium_airport"):
        for a in ranked:
            d = haversine_km(lat, lon, a["lat"], a["lon"])
            if d > max_km:
                break
            if a["type"] == wanted:
                return {**a, "distance_km": round(d)}
    if ranked:
        a = ranked[0]
        return {**a, "distance_km": round(haversine_km(lat, lon, a["lat"], a["lon"]))}
    return None
