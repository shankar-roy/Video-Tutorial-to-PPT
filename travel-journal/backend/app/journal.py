"""Fact-only journal copy. The person rewrites it in the app."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.clustering import PhotoPoint, haversine_km, select_candidate_photos


def format_title(city: str, start: date, end: date) -> str:
    label = city.strip() or "Trip"
    if start == end:
        return f"{label}, {start.day} {_month_year(start)}"
    if start.year == end.year and start.month == end.month:
        return f"{label}, {start.day}–{end.day} {_month_year(end)}"
    if start.year == end.year:
        return f"{label}, {start.day} {start.strftime('%b')}–{end.day} {_month_year(end)}"
    return (
        f"{label}, {start.day} {start.strftime('%b')} {start.year}"
        f"–{end.day} {_month_year(end)}"
    )


def draft_paragraph(day: date, place_names: list[str], flights, lodgings) -> str:
    sentences: list[str] = []
    for flight in flights:
        if flight.departure_local_date == day:
            label = " ".join(part for part in (flight.airline.strip(), flight.flight_number.strip()) if part)
            sentences.append(f"Flew {label} from {flight.origin_iata} to {flight.destination_iata}.")
        elif flight.arrival_local_date == day:
            sentences.append(f"Arrived at {flight.destination_iata}.")
    names = [name for name in _unique(place_names) if name]
    if len(names) == 1:
        sentences.append(f"Spent the day in {names[0]}.")
    elif len(names) > 1:
        sentences.append(f"Spent the day in {', '.join(names[:-1])} and {names[-1]}.")
    for lodging in lodgings:
        if lodging.check_in_date == day:
            sentences.append(f"Checked in at {lodging.name}.")
    if not sentences:
        sentences.append("No ticket or located photos for this day. It still belongs to the trip.")
    return " ".join(sentences)


def each_date(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def candidate_points(photos: list[PhotoPoint]) -> list[PhotoPoint]:
    return select_candidate_photos(photos)


def build_route(home: tuple[str, float, float] | None, events: list[tuple[datetime, str, float, float, str]]):
    """Polyline: home, then airports and stays in time order, then home again."""
    ordered = sorted(events, key=lambda item: _stamp(item[0]))
    points: list[dict] = []
    if home is not None:
        points.append({"name": home[0] or "Home", "latitude": home[1], "longitude": home[2], "kind": "home"})
    for _moment, name, latitude, longitude, kind in ordered:
        point = {"name": name or kind, "latitude": latitude, "longitude": longitude, "kind": kind}
        if points and haversine_km(points[-1]["latitude"], points[-1]["longitude"], latitude, longitude) < 1:
            continue
        points.append(point)
    if home is not None:
        closing = {"name": home[0] or "Home", "latitude": home[1], "longitude": home[2], "kind": "home"}
        if not points or haversine_km(points[-1]["latitude"], points[-1]["longitude"], closing["latitude"], closing["longitude"]) >= 1:
            points.append(closing)
    return points


def _month_year(value: date) -> str:
    return f"{value.strftime('%b')} {value.year}"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def _stamp(value: datetime) -> float:
    if value.tzinfo is None:
        from datetime import timezone

        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
