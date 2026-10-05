"""Exchange rates via Frankfurter (ECB reference rates, free, no key)."""

from __future__ import annotations

from .http import client

FRANKFURTER_URLS = ["https://api.frankfurter.dev/v1/latest", "https://api.frankfurter.app/latest"]

_rates: dict[tuple[str, str], float] = {}


async def get_rate(base: str, target: str) -> float:
    base, target = base.upper(), target.upper()
    if base == target:
        return 1.0
    if (base, target) in _rates:
        return _rates[(base, target)]
    last_error = None
    for url in FRANKFURTER_URLS:
        try:
            resp = await client().get(url, params={"base": base, "symbols": target})
            resp.raise_for_status()
            rate = resp.json()["rates"][target]
            _rates[(base, target)] = rate
            return rate
        except Exception as e:
            last_error = e
    raise ValueError(f"No exchange rate for {base}->{target} ({last_error}). Estimate the rate yourself and say so.")


async def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    rate = await get_rate(from_currency, to_currency)
    return {
        "amount": amount,
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "rate": rate,
        "converted": round(amount * rate, 2),
        "source": "ECB reference rate via Frankfurter",
    }
