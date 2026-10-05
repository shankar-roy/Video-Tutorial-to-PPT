"""Join flight tickets and photo stays into draft trips."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.airports import HOME_AIRPORT_MAX_KM, Airports
from app.clustering import PhotoPoint, haversine_km, night_key
from app.journal import build_route, draft_paragraph, each_date, format_title, select_candidate_photos
from app.models import (
    Flight,
    JournalDay,
    JournalPhoto,
    Lodging,
    Member,
    MergeSuggestion,
    Stay,
    StayPhoto,
    Trip,
    TripSource,
)

AWAY_CONNECT_GAP = timedelta(hours=36)
MIN_AWAY = timedelta(hours=24)
NEAR_HOME_KM_FLOOR = 80.0
SOURCE_MATCH_SECONDS = 36 * 3600
MERGE_PLACE_KM = 50.0


@dataclass
class Home:
    latitude: float
    longitude: float
    radius_km: float
    city: str
    airport_iata: str


@dataclass
class FlightView:
    id: int
    origin_iata: str
    destination_iata: str
    origin_lat: float | None
    origin_lon: float | None
    destination_lat: float | None
    destination_lon: float | None
    departure_at: datetime
    arrival_at: datetime | None
    departure_local_date: date
    arrival_local_date: date | None
    airline: str = ""
    flight_number: str = ""


@dataclass
class StayView:
    id: int
    start_at: datetime
    end_at: datetime
    start_date: date
    end_date: date
    latitude: float
    longitude: float
    place_name: str
    photo_count: int
    photos: list[PhotoPoint] = field(default_factory=list)


@dataclass
class Window:
    start_at: datetime
    end_at: datetime
    start_date: date
    end_date: date
    city: str
    night_count: int
    dest_iata: str = ""


def build_windows(home: Home, flights: list[FlightView], stays: list[StayView], airports: Airports) -> list[Window]:
    outbounds = sorted(
        (flight for flight in flights if flight.departure_at and _departs_home(flight, home, airports)),
        key=lambda flight: flight.departure_at,
    )
    inbounds = sorted(
        (
            flight
            for flight in flights
            if flight.arrival_at and _arrives_home(flight, home, airports) and not _departs_home(flight, home, airports)
        ),
        key=lambda flight: flight.arrival_at,
    )
    windows: list[Window] = []
    used_inbounds: set[int] = set()
    for outbound in outbounds:
        inbound = next(
            (
                flight
                for flight in inbounds
                if flight.id not in used_inbounds and flight.arrival_at >= outbound.departure_at
            ),
            None,
        )
        if inbound is not None:
            used_inbounds.add(inbound.id)
            end_at = inbound.arrival_at
            end_date = inbound.arrival_local_date or inbound.arrival_at.date()
        else:
            end_at, end_date = _infer_return(outbound, stays, home)
        start_date = outbound.departure_local_date
        away = _away_overlapping(stays, home, start_date, end_date)
        city, nights = _city_for(away, outbound.destination_iata, airports)
        windows.append(
            Window(
                start_at=outbound.departure_at,
                end_at=end_at,
                start_date=start_date,
                end_date=end_date,
                city=city,
                night_count=nights,
                dest_iata=outbound.destination_iata,
            )
        )

    covered = list(windows)
    for group in _away_groups(stays, home):
        start_at = group[0].start_at
        end_at = group[-1].end_at
        if end_at - start_at <= MIN_AWAY:
            continue
        start_date = group[0].start_date
        end_date = group[-1].end_date
        if any(_dates_overlap(start_date, end_date, window.start_date, window.end_date) for window in covered):
            continue
        city, nights = _city_for(group, "", airports)
        windows.append(
            Window(
                start_at=start_at,
                end_at=end_at,
                start_date=start_date,
                end_date=end_date,
                city=city,
                night_count=nights,
            )
        )
    return _merge_windows(windows)


def assemble_member(db, member: Member, airports: Airports) -> list[Trip]:
    if member.home_latitude is None or member.home_longitude is None or member.home_radius_km is None:
        raise ValueError("Confirm your home before building trips")
    home = Home(
        latitude=member.home_latitude,
        longitude=member.home_longitude,
        radius_km=member.home_radius_km,
        city=member.home_city,
        airport_iata=member.home_airport_iata,
    )
    flights = [_flight_view(row) for row in db.query(Flight).filter_by(member_id=member.id).all()]
    stay_rows = db.query(Stay).filter_by(member_id=member.id).all()
    stays = [_stay_view(row) for row in stay_rows]
    windows = build_windows(home, flights, stays, airports)

    sources = db.query(TripSource).filter_by(member_id=member.id).all()
    used: set[int] = set()
    affected: list[Trip] = []
    assignments: dict[int, int] = {}
    for window in windows:
        source = _match_source(sources, window.start_at, used)
        if source is None:
            trip = Trip(
                household_id=member.household_id,
                title=format_title(window.city, window.start_date, window.end_date),
                start_at=window.start_at,
                end_at=window.end_at,
                start_date=window.start_date,
                end_date=window.end_date,
                primary_city=window.city,
                status="draft",
            )
            db.add(trip)
            db.flush()
            source = TripSource(
                trip_id=trip.id,
                member_id=member.id,
                start_at=window.start_at,
                end_at=window.end_at,
                start_date=window.start_date,
                end_date=window.end_date,
                primary_city=window.city,
                night_count=window.night_count,
            )
            db.add(source)
            db.flush()
        else:
            source.start_at = window.start_at
            source.end_at = window.end_at
            source.start_date = window.start_date
            source.end_date = window.end_date
            source.primary_city = window.city
            source.night_count = window.night_count
            trip = source.trip
        used.add(source.id)
        affected.append(trip)
        for stay in stays:
            if _stay_in_window(stay, home, window):
                assignments[stay.id] = trip.id

    owned_trip_ids = {source.trip_id for source in sources}
    for stay in stay_rows:
        if stay.id in assignments:
            stay.trip_id = assignments[stay.id]
        elif stay.trip_id in owned_trip_ids:
            stay.trip_id = None

    for source in list(sources):
        if source.id in used:
            continue
        trip = source.trip
        db.delete(source)
        db.flush()
        remaining = db.query(TripSource).filter_by(trip_id=trip.id).count()
        if remaining == 0 and not any(day.edited for day in trip.days):
            _delete_trip(db, trip)
            affected = [item for item in affected if item.id != trip.id]

    seen: set[int] = set()
    refreshed: list[Trip] = []
    for trip in affected:
        if trip.id in seen:
            continue
        seen.add(trip.id)
        db.refresh(trip)
        if not trip.sources:
            continue
        _refresh_bounds(trip)
        rebuild_journal(db, trip, airports)
        refreshed.append(trip)
    refresh_suggestions(db, member.household_id)
    db.commit()
    return refreshed


def rebuild_journal(db, trip: Trip, airports: Airports) -> None:
    if not trip.sources:
        return
    _refresh_bounds(trip)
    member_ids = {source.member_id for source in trip.sources}
    stays = (
        db.query(Stay)
        .filter(Stay.member_id.in_(member_ids), Stay.trip_id == trip.id)
        .all()
    )
    city, _nights = _city_for([_stay_view(stay) for stay in stays], "", airports)
    if city == "Trip":
        cities = [source.primary_city for source in trip.sources if source.primary_city]
        city = cities[0] if cities else "Trip"
    trip.primary_city = city
    if not trip.title_edited:
        trip.title = format_title(city, trip.start_date, trip.end_date)

    flights = (
        db.query(Flight)
        .filter(Flight.member_id.in_(member_ids))
        .all()
    )
    flights = [
        flight
        for flight in flights
        if _date_in(flight.departure_local_date, trip.start_date, trip.end_date)
        or (flight.arrival_local_date and _date_in(flight.arrival_local_date, trip.start_date, trip.end_date))
    ]
    lodgings = (
        db.query(Lodging)
        .filter(Lodging.member_id.in_(member_ids))
        .all()
    )
    lodgings = [
        lodging for lodging in lodgings if _date_in(lodging.check_in_date, trip.start_date, trip.end_date)
    ]
    existing = {day.day: day for day in trip.days}
    wanted = set(each_date(trip.start_date, trip.end_date))
    for day in wanted:
        place_names = []
        for stay in stays:
            if stay.start_date <= day <= stay.end_date and stay.place_name:
                place_names.append(stay.place_name)
        day_flights = [
            flight
            for flight in flights
            if flight.departure_local_date == day or flight.arrival_local_date == day
        ]
        day_lodgings = [lodging for lodging in lodgings if lodging.check_in_date == day]
        draft = draft_paragraph(day, place_names, day_flights, day_lodgings)
        row = existing.get(day)
        if row is None:
            row = JournalDay(
                trip_id=trip.id,
                day=day,
                place_names=json.dumps(_unique(place_names)),
                body=draft,
                draft_body=draft,
                edited=False,
            )
            db.add(row)
            db.flush()
        else:
            row.place_names = json.dumps(_unique(place_names))
            row.draft_body = draft
            if not row.edited:
                row.body = draft
        points = []
        photos = []
        for stay in stays:
            for photo in stay.photos:
                if photo.taken_date == day:
                    points.append(
                        PhotoPoint(photo.asset_id, photo.taken_at, photo.latitude, photo.longitude)
                    )
                    photos.append(photo)
        chosen = {point.asset_id for point in select_candidate_photos(points)}
        have = {photo.asset_id: photo for photo in row.photos}
        for photo in list(row.photos):
            if photo.candidate and not photo.thumbnail_path and photo.asset_id not in chosen and photo.selected:
                db.delete(photo)
        for photo in photos:
            if photo.asset_id not in chosen or photo.asset_id in have:
                continue
            db.add(
                JournalPhoto(
                    journal_day_id=row.id,
                    member_id=photo.stay.member_id,
                    asset_id=photo.asset_id,
                    taken_at=photo.taken_at,
                    latitude=photo.latitude,
                    longitude=photo.longitude,
                    selected=True,
                    candidate=True,
                )
            )
    for day, row in existing.items():
        if day not in wanted and not row.edited:
            db.delete(row)
    db.flush()


def trip_route(db, trip: Trip, airports: Airports) -> list[dict]:
    member_ids = {source.member_id for source in trip.sources}
    if not member_ids:
        return []
    first_member_id = min(trip.sources, key=lambda source: source.start_at).member_id
    member = db.get(Member, first_member_id)
    home = None
    if member and member.home_latitude is not None:
        home = (member.home_city or "Home", member.home_latitude, member.home_longitude)
    events = []
    flights = db.query(Flight).filter(Flight.member_id.in_(member_ids)).all()
    for flight in flights:
        if not _date_in(flight.departure_local_date, trip.start_date, trip.end_date):
            continue
        if flight.origin_lat is not None:
            events.append((flight.departure_at, flight.origin_iata, flight.origin_lat, flight.origin_lon, "airport"))
        if flight.destination_lat is not None and flight.arrival_at is not None:
            events.append(
                (flight.arrival_at, flight.destination_iata, flight.destination_lat, flight.destination_lon, "airport")
            )
    stays = db.query(Stay).filter(Stay.trip_id == trip.id).all()
    for stay in stays:
        events.append((stay.start_at, stay.place_name or "Stay", stay.latitude, stay.longitude, "stay"))
    return build_route(home, events)


def refresh_suggestions(db, household_id: int) -> None:
    trips = db.query(Trip).filter_by(household_id=household_id).all()
    trips = [trip for trip in trips if trip.sources]
    qualifying: set[tuple[int, int]] = set()
    for index, left in enumerate(trips):
        for right in trips[index + 1 :]:
            if not _merge_candidate(db, left, right):
                continue
            qualifying.add((min(left.id, right.id), max(left.id, right.id)))
    existing = db.query(MergeSuggestion).filter_by(household_id=household_id).all()
    seen = set()
    for suggestion in existing:
        pair = (suggestion.trip_low_id, suggestion.trip_high_id)
        seen.add(pair)
        if suggestion.status == "pending" and pair not in qualifying:
            db.delete(suggestion)
    for low, high in qualifying:
        if (low, high) in seen:
            continue
        db.add(
            MergeSuggestion(
                household_id=household_id,
                trip_low_id=low,
                trip_high_id=high,
                status="pending",
            )
        )
    db.flush()


def accept_merge(db, suggestion: MergeSuggestion, airports: Airports) -> Trip:
    survivor = db.get(Trip, suggestion.trip_low_id)
    other = db.get(Trip, suggestion.trip_high_id)
    if survivor is None or other is None:
        raise ValueError("Trip not found")
    for source in list(other.sources):
        source.trip_id = survivor.id
    for stay in db.query(Stay).filter_by(trip_id=other.id).all():
        stay.trip_id = survivor.id
    for day in list(other.days):
        current = next((item for item in survivor.days if item.day == day.day), None)
        if current is None:
            day.trip_id = survivor.id
            continue
        if day.edited and current.edited:
            current.body = current.body.rstrip() + "\n\n" + day.body
            current.edited = True
        elif day.edited and not current.edited:
            current.body = day.body
            current.edited = True
        for photo in list(day.photos):
            photo.journal_day_id = current.id
        db.delete(day)
    db.flush()
    db.expire_all()
    other = db.get(Trip, suggestion.trip_high_id)
    survivor = db.get(Trip, suggestion.trip_low_id)
    _delete_trip(db, other)
    db.flush()
    _refresh_bounds(survivor)
    rebuild_journal(db, survivor, airports)
    db.commit()
    return survivor


def _merge_candidate(db, left: Trip, right: Trip) -> bool:
    left_members = {source.member_id for source in left.sources}
    right_members = {source.member_id for source in right.sources}
    if left_members & right_members:
        return False
    if not _dates_overlap(left.start_date, left.end_date, right.start_date, right.end_date):
        return False
    if left.primary_city and right.primary_city and left.primary_city.casefold() == right.primary_city.casefold():
        return True
    left_flights = _destination_codes(db, left)
    right_flights = _destination_codes(db, right)
    if left_flights & right_flights:
        return True
    left_stays = db.query(Stay).filter_by(trip_id=left.id).all()
    right_stays = db.query(Stay).filter_by(trip_id=right.id).all()
    for stay in left_stays:
        for other in right_stays:
            if haversine_km(stay.latitude, stay.longitude, other.latitude, other.longitude) <= MERGE_PLACE_KM:
                return True
    return False


def _destination_codes(db, trip: Trip) -> set[str]:
    member_ids = {source.member_id for source in trip.sources}
    if not member_ids:
        return set()
    flights = db.query(Flight).filter(Flight.member_id.in_(member_ids)).all()
    return {
        flight.destination_iata
        for flight in flights
        if _date_in(flight.departure_local_date, trip.start_date, trip.end_date)
    }


def _refresh_bounds(trip: Trip) -> None:
    trip.start_at = min(source.start_at for source in trip.sources)
    trip.end_at = max(source.end_at for source in trip.sources)
    trip.start_date = min(source.start_date for source in trip.sources)
    trip.end_date = max(source.end_date for source in trip.sources)


def _delete_trip(db, trip: Trip) -> None:
    trip_id = trip.id
    db.query(MergeSuggestion).filter(
        (MergeSuggestion.trip_low_id == trip_id) | (MergeSuggestion.trip_high_id == trip_id)
    ).delete(synchronize_session=False)
    day_ids = [row.id for row in db.query(JournalDay).filter_by(trip_id=trip_id).all()]
    if day_ids:
        db.query(JournalPhoto).filter(JournalPhoto.journal_day_id.in_(day_ids)).delete(synchronize_session=False)
        db.query(JournalDay).filter(JournalDay.id.in_(day_ids)).delete(synchronize_session=False)
    db.query(TripSource).filter_by(trip_id=trip_id).delete(synchronize_session=False)
    db.delete(trip)


def _flight_view(row: Flight) -> FlightView:
    return FlightView(
        id=row.id,
        origin_iata=row.origin_iata,
        destination_iata=row.destination_iata,
        origin_lat=row.origin_lat,
        origin_lon=row.origin_lon,
        destination_lat=row.destination_lat,
        destination_lon=row.destination_lon,
        departure_at=row.departure_at,
        arrival_at=row.arrival_at,
        departure_local_date=row.departure_local_date,
        arrival_local_date=row.arrival_local_date,
        airline=row.airline,
        flight_number=row.flight_number,
    )


def _stay_view(row: Stay) -> StayView:
    return StayView(
        id=row.id,
        start_at=row.start_at,
        end_at=row.end_at,
        start_date=row.start_date,
        end_date=row.end_date,
        latitude=row.latitude,
        longitude=row.longitude,
        place_name=row.place_name,
        photo_count=row.photo_count,
        photos=[
            PhotoPoint(photo.asset_id, photo.taken_at, photo.latitude, photo.longitude) for photo in row.photos
        ],
    )


def _departs_home(flight: FlightView, home: Home, airports: Airports) -> bool:
    if home.airport_iata and flight.origin_iata == home.airport_iata:
        return True
    return _near_home(flight.origin_iata, flight.origin_lat, flight.origin_lon, home, airports)


def _arrives_home(flight: FlightView, home: Home, airports: Airports) -> bool:
    if home.airport_iata and flight.destination_iata == home.airport_iata:
        return True
    return _near_home(flight.destination_iata, flight.destination_lat, flight.destination_lon, home, airports)


def _near_home(iata: str, latitude: float | None, longitude: float | None, home: Home, airports: Airports) -> bool:
    if latitude is None or longitude is None:
        airport = airports.get(iata)
        if airport is None:
            return False
        latitude = airport["lat"]
        longitude = airport["lon"]
    limit = max(home.radius_km, NEAR_HOME_KM_FLOOR)
    return haversine_km(latitude, longitude, home.latitude, home.longitude) <= limit


def _infer_return(outbound: FlightView, stays: list[StayView], home: Home) -> tuple[datetime, date]:
    later_home = sorted(
        (stay for stay in stays if stay.start_at > outbound.departure_at and not _is_away(stay, home)),
        key=lambda stay: stay.start_at,
    )
    if later_home:
        return later_home[0].start_at, later_home[0].start_date
    away = [stay for stay in stays if stay.start_at >= outbound.departure_at and _is_away(stay, home)]
    if away:
        last = max(away, key=lambda stay: stay.end_at)
        return last.end_at, last.end_date
    end = outbound.arrival_at or outbound.departure_at
    return end, outbound.arrival_local_date or outbound.departure_local_date


def _away_groups(stays: list[StayView], home: Home) -> list[list[StayView]]:
    groups: list[list[StayView]] = []
    current: list[StayView] = []
    for stay in sorted(stays, key=lambda item: item.start_at):
        if not _is_away(stay, home):
            if current:
                groups.append(current)
                current = []
            continue
        if not current:
            current = [stay]
            continue
        if stay.start_at - current[-1].end_at <= AWAY_CONNECT_GAP:
            current.append(stay)
        else:
            groups.append(current)
            current = [stay]
    if current:
        groups.append(current)
    return groups


def _away_overlapping(stays: list[StayView], home: Home, start_date: date, end_date: date) -> list[StayView]:
    return [
        stay
        for stay in stays
        if _is_away(stay, home) and _dates_overlap(stay.start_date, stay.end_date, start_date, end_date)
    ]


def _stay_in_window(stay: StayView, home: Home, window: Window) -> bool:
    return _is_away(stay, home) and _dates_overlap(
        stay.start_date, stay.end_date, window.start_date, window.end_date
    )


def _is_away(stay: StayView, home: Home) -> bool:
    return haversine_km(stay.latitude, stay.longitude, home.latitude, home.longitude) > home.radius_km


def _city_for(stays: list[StayView], dest_iata: str, airports: Airports) -> tuple[str, int]:
    scores: dict[str, list[int]] = {}
    for stay in stays:
        nights = {night_key(photo.taken_at) for photo in stay.photos}
        nights.discard(None)
        name = stay.place_name.strip()
        if not name and dest_iata:
            airport = airports.get(dest_iata)
            name = (airport or {}).get("city") or dest_iata
        if not name:
            continue
        bucket = scores.setdefault(name, [0, 0])
        bucket[0] += len(nights)
        bucket[1] += stay.photo_count or len(stay.photos)
    if scores:
        name, (nights, _photos) = max(scores.items(), key=lambda item: (item[1][0], item[1][1]))
        return name, nights
    airport = airports.get(dest_iata) if dest_iata else None
    if airport and airport.get("city"):
        return airport["city"], 0
    if dest_iata:
        return dest_iata, 0
    return "Trip", 0


def _merge_windows(windows: list[Window]) -> list[Window]:
    ordered = sorted(windows, key=lambda window: (window.start_date, window.start_at))
    merged: list[Window] = []
    for window in ordered:
        if not merged or window.start_date > merged[-1].end_date:
            merged.append(window)
            continue
        current = merged[-1]
        if window.end_at > current.end_at:
            current.end_at = window.end_at
        if window.end_date > current.end_date:
            current.end_date = window.end_date
        if window.night_count > current.night_count and window.city:
            current.city = window.city
            current.night_count = window.night_count
            current.dest_iata = window.dest_iata or current.dest_iata
    return merged


def _match_source(sources: list[TripSource], start_at: datetime, used: set[int]) -> TripSource | None:
    best = None
    best_delta = None
    for source in sources:
        if source.id in used:
            continue
        delta = abs((source.start_at - start_at).total_seconds())
        if delta <= SOURCE_MATCH_SECONDS and (best_delta is None or delta < best_delta):
            best = source
            best_delta = delta
    return best


def _dates_overlap(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    return start_a <= end_b and start_b <= end_a


def _date_in(day: date, start: date, end: date) -> bool:
    return start <= day <= end


def _unique(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered
