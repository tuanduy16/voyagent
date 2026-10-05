"""Weather via Open-Meteo: real forecast for the next ~2 weeks, otherwise a
"typical conditions" estimate from the same dates in the previous 3 years."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from statistics import mean

from .http import client

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_HORIZON_DAYS = 15
HISTORY_YEARS = 3

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def _daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - years, day=28)


async def _forecast(lat: float, lon: float, start: date, end: date) -> dict[date, dict]:
    resp = await client().get(
        FORECAST_URL,
        params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        },
    )
    resp.raise_for_status()
    d = resp.json()["daily"]
    return {
        date.fromisoformat(t): {
            "condition": WMO_CODES.get(code, f"WMO {code}"),
            "temp_high_c": hi, "temp_low_c": lo, "rain_chance_pct": pop, "source": "forecast",
        }
        for t, code, hi, lo, pop in zip(
            d["time"], d["weather_code"], d["temperature_2m_max"], d["temperature_2m_min"],
            d["precipitation_probability_max"],
        )
    }


async def _historical(lat: float, lon: float, days: list[date]) -> dict[date, dict]:
    samples: dict[date, list[tuple]] = {d: [] for d in days}
    for years_back in range(1, HISTORY_YEARS + 1):
        start, end = _shift_year(days[0], years_back), _shift_year(days[-1], years_back)
        resp = await client().get(
            ARCHIVE_URL,
            params={
                "latitude": lat, "longitude": lon, "timezone": "auto",
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
            },
        )
        resp.raise_for_status()
        d = resp.json()["daily"]
        by_date = {
            t: (code, hi, lo, rain)
            for t, code, hi, lo, rain in zip(
                d["time"], d["weather_code"], d["temperature_2m_max"], d["temperature_2m_min"], d["precipitation_sum"]
            )
        }
        for day in days:
            row = by_date.get(_shift_year(day, years_back).isoformat())
            if row and row[1] is not None:
                samples[day].append(row)

    out = {}
    for day, rows in samples.items():
        if not rows:
            continue
        code = Counter(r[0] for r in rows).most_common(1)[0][0]
        out[day] = {
            "condition": "Typically " + WMO_CODES.get(code, f"WMO {code}").lower(),
            "temp_high_c": round(mean(r[1] for r in rows), 1),
            "temp_low_c": round(mean(r[2] for r in rows if r[2] is not None), 1),
            "rain_chance_pct": round(100 * sum(1 for r in rows if (r[3] or 0) >= 1.0) / len(rows)),
            "source": f"historical ({len(rows)}-year average for this date)",
        }
    return out


async def get_weather(latitude: float, longitude: float, start_date: str, end_date: str) -> dict:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    days = _daterange(start, end)
    today = date.today()
    horizon = today + timedelta(days=FORECAST_HORIZON_DAYS)

    data: dict[date, dict] = {}
    forecast_days = [d for d in days if today <= d <= horizon]
    if forecast_days:
        data.update(await _forecast(latitude, longitude, forecast_days[0], forecast_days[-1]))
    missing = [d for d in days if d not in data]
    if missing:
        data.update(await _historical(latitude, longitude, missing))

    mode = {"forecast" if v["source"] == "forecast" else "historical_typical" for v in data.values()}
    return {
        "mode": mode.pop() if len(mode) == 1 else "mixed",
        "note": (
            "Days marked 'forecast' are a live forecast. Others are typical conditions averaged from "
            "the same dates in previous years, not a forecast."
        ),
        "days": [{"date": d.isoformat(), **data[d]} for d in days if d in data],
    }
