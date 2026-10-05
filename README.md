# Voyagent ✈️

**A multi-agent AI travel planner.** Give it an origin, destination, dates, budget and interests. A central orchestrator agent coordinates five specialist agents (flights, hotels, weather, activities, pricing) and produces a day-by-day travel plan with booking links, as **HTML**, **Markdown** and **JSON**.

Built with Python, asyncio, Pydantic and the Claude API (`claude-opus-5-5`). It runs on an Anthropic API key or on a Claude Pro/Max subscription via Claude Code. A full multi-day plan takes about 4–5 minutes.

## How it works

```
                         ┌────────────────────────────┐
   TripRequest  ───────▶ │  ORCHESTRATOR (brief)       │  airports, local currency,
                         │  splits budget by category  │  per-agent guidance
                         └─────────────┬──────────────┘
              ┌────────────────────────┼─────────────────────────┐   (run concurrently)
              ▼                        ▼                         ▼
     ┌────────────────┐      ┌────────────────┐       ┌────────────────┐
     │ FLIGHTS agent  │      │ HOTELS agent   │       │ WEATHER agent  │
     │ SerpApi/web    │      │ SerpApi/web    │       │ Open-Meteo     │
     └───────┬────────┘      └───────┬────────┘       └───────┬────────┘
             │                       │                        ▼
             │                       │               ┌────────────────┐
             │                       │               │ ACTIVITIES     │ weather-aware
             │                       │               │ Wikipedia/OSM  │ + web search
             │                       │               └───────┬────────┘
             └───────────────┬───────┴────────────────────────┘
                             ▼
                   ┌────────────────────┐   over budget?  ┌──────────────────────┐
                   │ PRICING agent       │ ──────────────▶ │ orchestrator reruns  │
                   │ selects + costs it  │ ◀────────────── │ flights/hotels w/caps│
                   └─────────┬──────────┘                 └──────────────────────┘
                             ▼
                   ┌────────────────────┐
                   │ ORCHESTRATOR        │ ─▶ plan.html / plan.md / plan.json
                   │ writes itinerary    │    with booking links
                   └────────────────────┘
```

Each agent is a Claude tool-use loop (`voyagent/agent.py`) with only the tools it needs. It finishes by calling a `submit_result` tool whose schema is a Pydantic model, so the orchestrator always receives validated, typed data. If a submission fails validation, the errors go back to the agent and it fixes them.

**Guardrails**
- **Booking links are built in code** (`services/links.py`): Google Flights, Skyscanner, Kayak, Booking.com, Expedia, Google Hotels, Airbnb, GetYourGuide, Viator and Google Maps, pre-filled with your dates and party size. The LLM never writes them.
- **Source URLs are checked.** A `source_url` an agent reports is kept only if it actually appeared in a tool or web-search result. Anything else is removed.
- **Totals are recomputed in code.** The plan total, remaining budget, within-budget flag and option indices are recalculated from the pricing agent's line items rather than taken from its arithmetic.
- **Estimates are labeled.** Prices that don't come from a live API or a web search result are marked "estimate".
- **Failures are contained.** If one specialist fails, the plan is still produced with a warning instead of crashing.

## Data sources

| Agent | Source | Key needed |
|---|---|---|
| Weather | [Open-Meteo](https://open-meteo.com): live forecast if the trip is within about 15 days, otherwise a 3-year average for the same dates | No |
| Activities | Wikipedia GeoSearch (named sights), OpenStreetMap Overpass (restaurants and bars, best-effort), Claude web search (tickets, events) | No |
| Currency | [Frankfurter](https://frankfurter.dev) (ECB rates) | No |
| Flights / Hotels | [SerpApi](https://serpapi.com) Google Flights / Google Hotels **if `SERPAPI_API_KEY` is set**, otherwise Claude web search | Optional |

## Setup

```powershell
git clone https://github.com/tuanduy16/voyagent.git
cd voyagent
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                 # then fill in ANTHROPIC_API_KEY (and SERPAPI_API_KEY if you have one)
```

### Using your Claude Pro/Max subscription instead of an API key

Add `--subscription` to any command. Each agent then runs through the Claude Code CLI (`claude -p`) under your Claude login, with no API key or API billing; it uses your plan's usage limits instead. Requirements: Claude Code installed and logged in (run `claude` once). This mode is for your own personal use.

In this mode, Claude Code can't call the project's Python tools, so each agent's data is fetched up front and included in its prompt: weather, Wikipedia/OSM places for your interests, exchange rates, and SerpApi results if configured. Agents still use web search, and outputs are schema-checked and URL-verified the same way.

## Usage

```powershell
python -m voyagent `
  --origin "Boston, USA" --destination "Lisbon, Portugal" `
  --start 2026-11-10 --end 2026-11-15 `
  --budget 4000 --currency USD --travelers 2 `
  --interests food history nightlife `
  --style balanced --notes "no red-eyes; one vegetarian" `
  --open
```

| Flag | Meaning |
|---|---|
| `--interests` | Free-form, e.g. `museums art food nightlife nature family views architecture` |
| `--style` | `budget`, `balanced` or `luxury` |
| `--subscription` | Use your Claude Pro/Max login via Claude Code instead of `ANTHROPIC_API_KEY` |
| `--no-web-search` | Turn off Claude web search (cheaper; prices become estimates unless SerpApi is set) |
| `--revisions N` | Maximum over-budget re-planning rounds (default 1) |
| `--open` | Open the HTML plan when it's done |
| `-v` | Debug logging |

Output goes to `output/<city>-<date>-<time>/plan.{html,md,json}`.

A run is 7 agent runs (up to 10 with a revision round). Each run makes a few Claude calls and possibly some web searches, so expect a few minutes per plan. To lower cost, set `VOYAGENT_SPECIALIST_MODEL=claude-sonnet-5-5` in `.env` so the specialists run on Sonnet while the orchestrator stays on Opus.

## Tests

```powershell
python tests/test_offline.py     # agent loop + full orchestration with scripted model responses (no API key)
python tests/smoke_services.py   # hits the free weather/places/currency APIs
```

`test_offline.py` also writes a sample plan to `output/sample/plan.html`, so you can see the output format without an API key.

## Project layout

```
voyagent/
  __main__.py          CLI
  orchestrator.py      central coordinator: brief -> fan-out -> pricing -> revision -> itinerary
  agent.py             shared agent runtime (tool loop, submit_result, URL verification)
  models.py            Pydantic contracts between agents
  agents/specialists.py   flights, hotels, weather, activities, pricing agents
  agents/tools.py      tool schemas wrapping the services
  services/            geo, weather, places, currency, serpapi, links (booking deep links)
  render.py            HTML + Markdown output
```

## Ideas for next steps
- Multi-city trips (one brief per leg, with transport between legs)
- A web UI (FastAPI + this same orchestrator) that streams agent progress
- Use an affiliate API such as Duffel, Amadeus or Booking for real bookable offers instead of search deep links

## License

MIT © Duy Pham
