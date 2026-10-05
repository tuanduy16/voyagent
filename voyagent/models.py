"""Data contracts between the orchestrator and the specialist agents.

Agent output models have no default values on purpose: every field is part of the
`submit_result` tool schema the agent must fill in. Optional data is expressed as
`X | None` so the agent states "unknown" explicitly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DataSource = Literal["live_api", "web_search", "estimate"]


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class TripRequest(BaseModel):
    origin: str
    destination: str
    start_date: date
    end_date: date
    budget: float = Field(gt=0, description="Total budget for all travelers")
    currency: str = "USD"
    travelers: int = Field(default=1, ge=1, le=9)
    interests: list[str] = Field(default_factory=list)
    travel_style: Literal["budget", "balanced", "luxury"] = "balanced"
    notes: str = ""

    @model_validator(mode="after")
    def _check_dates(self) -> TripRequest:
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        self.currency = self.currency.upper()
        return self

    @property
    def nights(self) -> int:
        return (self.end_date - self.start_date).days


# ---------------------------------------------------------------------------
# Orchestrator brief
# ---------------------------------------------------------------------------


class BudgetAllocation(BaseModel):
    flights: float
    lodging: float
    activities: float
    food: float
    local_transport: float
    buffer: float

    def total(self) -> float:
        return self.flights + self.lodging + self.activities + self.food + self.local_transport + self.buffer


class TripBrief(BaseModel):
    origin_city: str
    origin_airport_iata: str = Field(description="Main IATA airport or metro code for the origin, e.g. BOS, NYC, LON")
    destination_city: str = Field(description="The city the trip is based in")
    destination_country: str
    destination_airport_iata: str
    destination_currency: str = Field(description="ISO 4217 code of the local currency")
    budget_allocation: BudgetAllocation = Field(
        description="Budget split in the traveler's currency, for all travelers combined; must sum to the total budget"
    )
    budget_feasibility: str = Field(description="Frank one-paragraph assessment of whether the budget is realistic")
    flight_guidance: str
    hotel_guidance: str
    activity_guidance: str


# ---------------------------------------------------------------------------
# Specialist outputs
# ---------------------------------------------------------------------------


class FlightOption(BaseModel):
    airline: str
    outbound_summary: str = Field(description="e.g. 'Nov 10, BOS 21:35 -> LIS 09:05+1, nonstop, 6h30m'")
    return_summary: str | None = Field(description="Return leg summary if known")
    stops: int
    price_total: float = Field(description="Round-trip price for ALL travelers, in the traveler's currency")
    price_is_estimate: bool
    cabin: str
    source_url: str | None = Field(description="Only a URL returned by a tool or web search; never invent one")
    notes: str


class FlightResult(BaseModel):
    options: list[FlightOption]
    recommended_index: int
    data_source: DataSource
    summary: str


class HotelOption(BaseModel):
    name: str
    neighborhood: str
    nightly_rate: float = Field(description="Per night for the whole party, in the traveler's currency")
    total_price: float = Field(description="Whole stay, all travelers, in the traveler's currency")
    price_is_estimate: bool
    rating: float | None = Field(description="Guest rating on a 0-5 or 0-10 scale as reported")
    stars: float | None
    why: str
    source_url: str | None = Field(description="Only a URL returned by a tool or web search; never invent one")


class HotelResult(BaseModel):
    options: list[HotelOption]
    recommended_index: int
    data_source: DataSource
    summary: str


class DayWeather(BaseModel):
    day: date
    condition: str
    temp_high_c: float | None
    temp_low_c: float | None
    rain_chance_pct: float | None


class WeatherResult(BaseModel):
    data_source: Literal["forecast", "historical_typical", "mixed"]
    summary: str
    daily: list[DayWeather]
    rainy_days: list[date]
    packing_tips: list[str]
    activity_advice: str


class Activity(BaseModel):
    name: str
    category: str = Field(description="e.g. museum, food, nightlife, nature, tour, landmark")
    description: str
    neighborhood: str
    est_cost_per_person: float = Field(description="In the traveler's currency; 0 if free")
    duration_hours: float
    indoor: bool
    requires_booking: bool
    best_time: str = Field(description="e.g. 'morning', 'sunset', 'Tue-Sun 10:00-18:00'")
    suggested_day: date | None
    booking_query: str = Field(description="Search phrase to find tickets/tours for this, e.g. 'Belem Tower tickets'")
    source_url: str | None = Field(description="Only a URL returned by a tool or web search; never invent one")


class ActivitiesResult(BaseModel):
    activities: list[Activity]
    events_during_trip: list[str] = Field(description="Festivals/events happening on the trip dates, if any were found")
    summary: str


class LineItem(BaseModel):
    category: Literal["flights", "lodging", "activities", "food", "local_transport", "other"]
    description: str
    amount: float = Field(description="In the traveler's currency, all travelers")


class RevisionRequest(BaseModel):
    rerun_flights: bool
    rerun_hotels: bool
    max_flight_total: float | None
    max_hotel_nightly: float | None
    guidance: str


class PricingResult(BaseModel):
    selected_flight_index: int | None
    selected_hotel_index: int | None
    selected_activities: list[str] = Field(description="Exact names of the activities that fit the budget")
    line_items: list[LineItem]
    total: float
    remaining: float
    within_budget: bool
    currency_notes: str
    savings_tips: list[str]
    revision: RevisionRequest


# ---------------------------------------------------------------------------
# Final itinerary
# ---------------------------------------------------------------------------


class ItineraryItem(BaseModel):
    time: str = Field(description="'09:00' or 'Morning'")
    title: str
    category: Literal["flight", "lodging", "activity", "food", "transport", "free_time"]
    description: str
    est_cost: float | None = Field(description="In the traveler's currency, all travelers")
    booking_query: str | None = Field(
        description="Search phrase used to build booking/map links, e.g. 'Time Out Market Lisbon'"
    )


class DayPlan(BaseModel):
    day: date
    title: str
    weather_note: str
    items: list[ItineraryItem]


class Itinerary(BaseModel):
    title: str
    overview: str
    days: list[DayPlan]
    booking_checklist: list[str] = Field(description="Ordered steps: what to book first and by when")
    packing_list: list[str]
    local_tips: list[str]


class TripPlan(BaseModel):
    request: TripRequest
    brief: TripBrief
    location: dict | None
    weather: WeatherResult | None
    flights: FlightResult | None
    hotels: HotelResult | None
    activities: ActivitiesResult | None
    pricing: PricingResult | None
    itinerary: Itinerary
    revisions: int
    warnings: list[str]
    generated_at: datetime

    @property
    def selected_flight(self) -> FlightOption | None:
        if not self.flights or not self.pricing or self.pricing.selected_flight_index is None:
            return None
        return self.flights.options[self.pricing.selected_flight_index]

    @property
    def selected_hotel(self) -> HotelOption | None:
        if not self.hotels or not self.pricing or self.pricing.selected_hotel_index is None:
            return None
        return self.hotels.options[self.pricing.selected_hotel_index]
