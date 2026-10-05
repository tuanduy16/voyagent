"""Render a TripPlan to a standalone HTML page and a Markdown file."""

from __future__ import annotations

from html import escape

from .models import ItineraryItem, TripPlan
from .services import links

CATEGORY_ICONS = {
    "flight": "✈️", "lodging": "🏨", "activity": "🎟️", "food": "🍽️", "transport": "🚆", "free_time": "☕",
}


def _money(plan: TripPlan, amount: float | None) -> str:
    return "—" if amount is None else f"{plan.request.currency} {amount:,.0f}"


def _flight_links(plan: TripPlan) -> list[dict]:
    r, b = plan.request, plan.brief
    return links.flight_links(b.origin_airport_iata, b.destination_airport_iata, r.start_date, r.end_date, r.travelers)


def _hotel_links(plan: TripPlan, name: str | None) -> list[dict]:
    r = plan.request
    return links.hotel_links(plan.brief.destination_city, r.start_date, r.end_date, r.travelers, name)


def item_links(plan: TripPlan, item: ItineraryItem) -> list[dict]:
    city = plan.brief.destination_city
    query = item.booking_query or item.title
    if item.category == "flight":
        return _flight_links(plan)[:2]
    if item.category == "lodging":
        hotel = plan.selected_hotel
        return _hotel_links(plan, hotel.name if hotel else None)[:2]
    if item.category == "activity":
        return links.activity_links(query, city)
    if item.category == "food":
        return [links.maps_link(f"{query} {city}" if city.lower() not in query.lower() else query)]
    return []


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e6e3dc;--accent:#0b6e4f;--accent-ink:#fff;
--warn-bg:#fff4e0;--warn-ink:#8a4b00;--chip:#eef4f1;--bar:#e9e6df;--over:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121314;--card:#1c1d1f;--ink:#ececec;
--muted:#a0a0a6;--line:#2c2d30;--accent:#4cc39a;--accent-ink:#06261b;--warn-bg:#3a2a10;--warn-ink:#ffcf8a;--chip:#1f2e28;--bar:#2c2d30;--over:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:2rem;line-height:1.2;margin:0 0 8px}h2{font-size:1.3rem;margin:40px 0 12px}h3{margin:0 0 4px;font-size:1.05rem}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:12px 0}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.stat b{display:block;font-size:1.35rem}.stat span{color:var(--muted);font-size:.85rem}
.warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:10px;padding:10px 14px;margin:8px 0}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.btn{display:inline-block;padding:5px 11px;border-radius:999px;background:var(--accent);color:var(--accent-ink);
text-decoration:none;font-size:.82rem;font-weight:600}.btn.ghost{background:var(--chip);color:var(--ink)}
.chip{display:inline-block;padding:2px 9px;border-radius:999px;background:var(--chip);font-size:.78rem;margin-right:4px}
.rec{border:2px solid var(--accent)}
.weather{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}
.wday{flex:0 0 112px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px;font-size:.85rem}
.wday b{display:block}
.day{background:var(--card);border:1px solid var(--line);border-radius:14px;margin:14px 0;overflow:hidden}
.day header{padding:14px 20px;border-bottom:1px solid var(--line)}
.item{display:grid;grid-template-columns:72px 1fr auto;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line)}
.item:last-child{border-bottom:0}.time{color:var(--muted);font-size:.85rem;padding-top:2px}
.cost{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--muted)}
table{width:100%;border-collapse:collapse}td,th{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:10px;border-radius:999px;background:var(--bar);overflow:hidden;margin:10px 0 4px}
.bar i{display:block;height:100%;background:var(--accent)}.bar.over i{background:var(--over)}
ul{padding-left:20px}
@media (max-width:560px){.item{grid-template-columns:1fr}.cost{justify-self:start}}
"""


def _btns(link_list: list[dict], ghost_after: int = 1) -> str:
    return '<div class="btns">' + "".join(
        f'<a class="btn{" ghost" if i >= ghost_after else ""}" href="{escape(l["url"])}" target="_blank" rel="noopener">'
        f"{escape(l['label'])}</a>"
        for i, l in enumerate(link_list)
    ) + "</div>"


def render_html(plan: TripPlan) -> str:
    r, b, it, p = plan.request, plan.brief, plan.itinerary, plan.pricing
    e = escape
    parts: list[str] = []
    add = parts.append

    add(f"<h1>{e(it.title)}</h1>")
    add(
        f'<p class="muted">{e(b.origin_city)} → {e(b.destination_city)}, {e(b.destination_country)} · '
        f"{r.start_date:%b %d} – {r.end_date:%b %d, %Y} · {r.travelers} traveler{'s' if r.travelers > 1 else ''} · "
        f"{e(', '.join(r.interests) or 'sightseeing')}</p>"
    )
    for w in plan.warnings:
        add(f'<div class="warn">⚠️ {e(w)}</div>')
    add(f'<div class="card">{e(it.overview)}</div>')

    # Budget summary
    if p:
        pct = min(100, p.total / r.budget * 100)
        add('<div class="grid">')
        add(f'<div class="card stat"><span>Estimated total</span><b>{_money(plan, p.total)}</b></div>')
        add(f'<div class="card stat"><span>Budget</span><b>{_money(plan, r.budget)}</b></div>')
        label = "Remaining" if p.remaining >= 0 else "Over budget"
        add(f'<div class="card stat"><span>{label}</span><b>{_money(plan, abs(p.remaining))}</b></div>')
        add("</div>")
        add(f'<div class="bar{"" if p.within_budget else " over"}"><i style="width:{pct:.1f}%"></i></div>')

    # Weather
    if plan.weather:
        w = plan.weather
        src = {"forecast": "Live forecast", "historical_typical": "Typical conditions (historical)", "mixed": "Forecast + typical conditions"}[w.data_source]
        add(f"<h2>🌤️ Weather</h2><p>{e(w.summary)} <span class='chip'>{src}</span></p><div class='weather'>")
        for d in w.daily:
            temps = f"{d.temp_high_c:.0f}° / {d.temp_low_c:.0f}°C" if d.temp_high_c is not None and d.temp_low_c is not None else ""
            rain = f"💧 {d.rain_chance_pct:.0f}%" if d.rain_chance_pct is not None else ""
            add(f"<div class='wday'><b>{d.day:%a %d}</b>{e(d.condition)}<br>{temps}<br>{rain}</div>")
        add("</div>")

    # Flights
    if plan.flights:
        add("<h2>✈️ Flights</h2>")
        add(f"<p class='muted'>{e(plan.flights.summary)}</p>")
        for i, f in enumerate(plan.flights.options):
            selected = p is not None and p.selected_flight_index == i
            est = " <span class='chip'>estimate</span>" if f.price_is_estimate else ""
            sel = " <span class='chip'>✓ selected</span>" if selected else ""
            add(f'<div class="card{" rec" if selected else ""}"><h3>{e(f.airline)} · {_money(plan, f.price_total)}{est}{sel}</h3>')
            add(f"<div>{e(f.outbound_summary)}</div>")
            if f.return_summary:
                add(f"<div>{e(f.return_summary)}</div>")
            add(f"<div class='muted'>{f.stops} stop(s) · {e(f.cabin)} · {e(f.notes)}</div>")
            extra = [{"label": "Source", "url": f.source_url}] if f.source_url else []
            add(_btns(_flight_links(plan) + extra) + "</div>")

    # Hotels
    if plan.hotels:
        add("<h2>🏨 Where to stay</h2>")
        add(f"<p class='muted'>{e(plan.hotels.summary)}</p>")
        for i, h in enumerate(plan.hotels.options):
            selected = p is not None and p.selected_hotel_index == i
            est = " <span class='chip'>estimate</span>" if h.price_is_estimate else ""
            sel = " <span class='chip'>✓ selected</span>" if selected else ""
            rating = f" · ★ {h.rating:g}" if h.rating else ""
            add(f'<div class="card{" rec" if selected else ""}"><h3>{e(h.name)}{sel}</h3>')
            add(f"<div>{e(h.neighborhood)}{rating} · {_money(plan, h.nightly_rate)}/night · {_money(plan, h.total_price)} total{est}</div>")
            add(f"<div class='muted'>{e(h.why)}</div>")
            extra = [{"label": "Source", "url": h.source_url}] if h.source_url else []
            add(_btns(_hotel_links(plan, h.name) + extra) + "</div>")
        add("<p class='muted'>Prefer an apartment?</p>" + _btns(_hotel_links(plan, None)[-1:], ghost_after=0))

    # Day by day
    add("<h2>🗓️ Day by day</h2>")
    for n, day in enumerate(it.days, 1):
        add(f'<section class="day"><header><h3>Day {n} · {day.day:%A, %b %d} — {e(day.title)}</h3>')
        add(f"<div class='muted'>{e(day.weather_note)}</div></header>")
        for item in day.items:
            icon = CATEGORY_ICONS.get(item.category, "•")
            add(f'<div class="item"><div class="time">{e(item.time)}</div><div><b>{icon} {e(item.title)}</b>')
            add(f"<div class='muted'>{e(item.description)}</div>")
            item_link_list = item_links(plan, item)
            if item_link_list:
                add(_btns(item_link_list))
            add(f'</div><div class="cost">{_money(plan, item.est_cost) if item.est_cost else ""}</div></div>')
        add("</section>")

    # Budget table
    if p:
        add("<h2>💰 Budget breakdown</h2><div class='card'><table><tr><th>Category</th><th>Item</th><th class='num'>Amount</th></tr>")
        for li in p.line_items:
            add(f"<tr><td>{e(li.category.replace('_', ' '))}</td><td>{e(li.description)}</td><td class='num'>{_money(plan, li.amount)}</td></tr>")
        add(f"<tr><th colspan='2'>Total</th><th class='num'>{_money(plan, p.total)}</th></tr></table>")
        add(f"<p class='muted'>{e(p.currency_notes)}</p></div>")
        if p.savings_tips:
            add("<h3>Ways to save</h3><ul>" + "".join(f"<li>{e(t)}</li>" for t in p.savings_tips) + "</ul>")

    add("<h2>✅ Booking checklist</h2><ol>" + "".join(f"<li>{e(s)}</li>" for s in it.booking_checklist) + "</ol>")
    if plan.weather and plan.weather.packing_tips:
        packing = list(dict.fromkeys(it.packing_list + plan.weather.packing_tips))
    else:
        packing = it.packing_list
    add("<div class='grid'>")
    add("<div class='card'><h3>🧳 Packing</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in packing) + "</ul></div>")
    add("<div class='card'><h3>💡 Local tips</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in it.local_tips) + "</ul></div>")
    add("</div>")
    add(
        f"<p class='muted'>Generated {plan.generated_at:%Y-%m-%d %H:%M} by Voyagent, a multi-agent travel planner. "
        "Prices change constantly; anything marked estimate is not a quote, so confirm on the booking site.</p>"
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(b.destination_city)} Trip Plan</title><style>{CSS}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>"
    )


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _md_links(link_list: list[dict]) -> str:
    return " · ".join(f"[{l['label']}]({l['url']})" for l in link_list)


def render_markdown(plan: TripPlan) -> str:
    r, b, it, p = plan.request, plan.brief, plan.itinerary, plan.pricing
    out = [f"# {it.title}", "", f"_{b.origin_city} → {b.destination_city}, {r.start_date} to {r.end_date}, {r.travelers} traveler(s)_", ""]
    out += [f"> ⚠️ {w}" for w in plan.warnings]
    out += ["", it.overview, ""]
    if p:
        out.append(f"**Estimated total:** {_money(plan, p.total)} of {_money(plan, r.budget)} budget ({'within' if p.within_budget else 'OVER'} budget)\n")

    if plan.flights and plan.selected_flight:
        f = plan.selected_flight
        out += ["## ✈️ Flight", f"**{f.airline}** · {_money(plan, f.price_total)}{' (estimate)' if f.price_is_estimate else ''}",
                f"- {f.outbound_summary}"] + ([f"- {f.return_summary}"] if f.return_summary else []) + [
                f"- Book: {_md_links(_flight_links(plan))}", ""]
    if plan.hotels and plan.selected_hotel:
        h = plan.selected_hotel
        out += ["## 🏨 Hotel", f"**{h.name}** ({h.neighborhood}) · {_money(plan, h.nightly_rate)}/night · {_money(plan, h.total_price)} total",
                f"- {h.why}", f"- Book: {_md_links(_hotel_links(plan, h.name))}", ""]
    if plan.weather:
        out += ["## 🌤️ Weather", plan.weather.summary, ""]

    out.append("## 🗓️ Itinerary")
    for n, day in enumerate(it.days, 1):
        out += ["", f"### Day {n} · {day.day:%a %b %d} — {day.title}", f"_{day.weather_note}_", ""]
        for item in day.items:
            cost = f" — {_money(plan, item.est_cost)}" if item.est_cost else ""
            lk = item_links(plan, item)
            out.append(f"- **{item.time}** {CATEGORY_ICONS.get(item.category, '')} {item.title}{cost}: {item.description}"
                       + (f" ({_md_links(lk)})" if lk else ""))

    if p:
        out += ["", "## 💰 Budget", "| Category | Item | Amount |", "|---|---|---:|"]
        out += [f"| {li.category} | {li.description} | {_money(plan, li.amount)} |" for li in p.line_items]
        out += [f"| **Total** | | **{_money(plan, p.total)}** |", ""]
        out += [f"- {t}" for t in p.savings_tips]
    out += ["", "## ✅ Booking checklist"] + [f"{i}. {s}" for i, s in enumerate(it.booking_checklist, 1)]
    out += ["", "## 🧳 Packing"] + [f"- {x}" for x in it.packing_list]
    out += ["", "## 💡 Local tips"] + [f"- {x}" for x in it.local_tips]
    return "\n".join(out) + "\n"
