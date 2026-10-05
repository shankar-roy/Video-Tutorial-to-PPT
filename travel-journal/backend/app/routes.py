"""HTTP API for the family journal."""

from __future__ import annotations

import io
import json
import secrets
from datetime import date, datetime
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.airports import load_airports
from app.assembly import _delete_trip, accept_merge, assemble_member, trip_route
from app.deps import get_db, get_member
from app.gmail import GmailError, ingest_messages, retry_failure
from app.models import (
    Flight,
    Household,
    JournalDay,
    JournalPhoto,
    Lodging,
    Member,
    MergeSuggestion,
    ParseFailure,
    Stay,
    StayPhoto,
    Trip,
    TripSource,
)
from app.security import AppleSignInError, create_oauth_state, create_session, read_oauth_state
from app.timeutil import wall_date

router = APIRouter()
MAX_STAYS = 5000
MAX_PHOTOS = 20000


class AppleLogin(BaseModel):
    identity_token: str
    display_name: str | None = None
    email: str | None = None


class DevLogin(BaseModel):
    sub: str = Field(min_length=1, max_length=80)
    display_name: str = "Traveler"


class HomeIn(BaseModel):
    city: str = Field(min_length=1, max_length=200)
    latitude: float
    longitude: float
    radius_km: float


class JoinIn(BaseModel):
    invite_code: str


class PhotoIn(BaseModel):
    asset_id: str = Field(min_length=1, max_length=200)
    taken_at: datetime
    latitude: float
    longitude: float


class StayIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    start_at: datetime
    end_at: datetime
    latitude: float
    longitude: float
    place_name: str = ""
    photos: list[PhotoIn]


class StaySync(BaseModel):
    stays: list[StayIn]


class ExchangeIn(BaseModel):
    code: str
    redirect_uri: str


class DayIn(BaseModel):
    body: str = Field(max_length=20000)


class TripIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def _check_coord(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise HTTPException(status_code=422, detail="Latitude or longitude is out of range")


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/auth/apple")
def auth_apple(body: AppleLogin, request: Request, db: Session = Depends(get_db)):
    try:
        claims = request.app.state.apple.verify(body.identity_token)
    except AppleSignInError:
        raise HTTPException(status_code=401, detail="Apple sign-in could not be verified") from None
    email = claims.get("email") or body.email or ""
    name = (body.display_name or "").strip()
    member = _login_member(db, sub=claims["sub"], email=email, display_name=name)
    return _auth_payload(member, request)


@router.post("/auth/dev")
def auth_dev(body: DevLogin, request: Request, db: Session = Depends(get_db)):
    if not request.app.state.settings.allow_dev_login:
        raise HTTPException(status_code=404, detail="Not found")
    member = _login_member(db, sub=f"dev:{body.sub}", email="", display_name=body.display_name.strip())
    return _auth_payload(member, request)


@router.get("/me")
def me(member: Member = Depends(get_member)):
    return _member_payload(member)


@router.put("/me/home")
def put_home(body: HomeIn, member: Member = Depends(get_member), db: Session = Depends(get_db)):
    _check_coord(body.latitude, body.longitude)
    if not 1 <= body.radius_km <= 200:
        raise HTTPException(status_code=422, detail="Home radius must be between 1 and 200 km")
    member.home_city = body.city.strip()
    member.home_latitude = body.latitude
    member.home_longitude = body.longitude
    member.home_radius_km = body.radius_km
    airport = load_airports().nearest(body.latitude, body.longitude)
    member.home_airport_iata = airport["iata"] if airport else ""
    db.commit()
    return _member_payload(member)


@router.post("/households/join")
def join_household(body: JoinIn, member: Member = Depends(get_member), db: Session = Depends(get_db)):
    target = db.query(Household).filter_by(invite_code=body.invite_code.strip()).one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Invite code not found")
    if member.household_id == target.id:
        return _member_payload(member)
    old_id = member.household_id
    sources = db.query(TripSource).filter_by(member_id=member.id).all()
    for source in list(sources):
        trip = db.get(Trip, source.trip_id)
        db.delete(source)
        db.flush()
        if trip is None:
            continue
        remaining = db.query(TripSource).filter_by(trip_id=trip.id).count()
        if remaining == 0:
            _delete_trip(db, trip)
    member.household_id = target.id
    db.flush()
    others = db.query(Member).filter(Member.household_id == old_id, Member.id != member.id).count()
    if others == 0:
        old = db.get(Household, old_id)
        if old is not None:
            db.delete(old)
    db.commit()
    db.refresh(member)
    return _member_payload(member)


@router.post("/me/stays")
def sync_stays(body: StaySync, member: Member = Depends(get_member), db: Session = Depends(get_db)):
    if len(body.stays) > MAX_STAYS:
        raise HTTPException(status_code=400, detail="Too many stays in one sync")
    photo_total = sum(len(stay.photos) for stay in body.stays)
    if photo_total > MAX_PHOTOS:
        raise HTTPException(status_code=400, detail="Too many photo locations in one sync")
    incoming = {stay.client_id: stay for stay in body.stays}
    for stay in db.query(Stay).filter_by(member_id=member.id).all():
        if stay.client_id not in incoming:
            db.delete(stay)
    db.flush()
    for item in body.stays:
        _check_coord(item.latitude, item.longitude)
        for photo in item.photos:
            _check_coord(photo.latitude, photo.longitude)
        row = db.query(Stay).filter_by(member_id=member.id, client_id=item.client_id).one_or_none()
        if row is None:
            row = Stay(member_id=member.id, client_id=item.client_id)
            db.add(row)
        row.start_at = item.start_at
        row.end_at = item.end_at
        row.start_date = wall_date(item.start_at)
        row.end_date = wall_date(item.end_at)
        row.latitude = item.latitude
        row.longitude = item.longitude
        row.place_name = item.place_name.strip()[:200]
        row.photo_count = len(item.photos)
        row.photos.clear()
        for photo in item.photos:
            row.photos.append(
                StayPhoto(
                    asset_id=photo.asset_id,
                    taken_at=photo.taken_at,
                    taken_date=wall_date(photo.taken_at),
                    latitude=photo.latitude,
                    longitude=photo.longitude,
                )
            )
    db.commit()
    return {"stays": len(body.stays)}


@router.get("/me/stays")
def list_stays(member: Member = Depends(get_member), db: Session = Depends(get_db)):
    rows = db.query(Stay).filter_by(member_id=member.id).order_by(Stay.start_at).all()
    return [
        {
            "client_id": row.client_id,
            "start_at": row.start_at,
            "end_at": row.end_at,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "place_name": row.place_name,
            "photo_count": row.photo_count,
            "trip_id": row.trip_id,
        }
        for row in rows
    ]


@router.get("/gmail/start")
def gmail_start(request: Request, member: Member = Depends(get_member)):
    gmail = request.app.state.gmail
    if not gmail.configured():
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    settings = request.app.state.settings
    redirect_uri = settings.public_base_url.rstrip("/") + "/gmail/callback"
    state = create_oauth_state(member.id, redirect_uri, settings.secret_key)
    return {"authorization_url": gmail.authorization_url(state, redirect_uri)}


@router.get("/gmail/callback")
def gmail_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    settings = request.app.state.settings
    try:
        payload = read_oauth_state(state, settings.secret_key)
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Gmail sync expired; start again") from None
    member = db.get(Member, int(payload["sub"]))
    if member is None:
        raise HTTPException(status_code=400, detail="Gmail sync expired; start again")
    stats = _sync_with_code(request, db, member, code, payload["redir"])
    query = f"flights={stats.flights}&lodgings={stats.lodgings}&failures={stats.failures}"
    return RedirectResponse(f"traveljournal://gmail-synced?{query}")


@router.post("/gmail/exchange")
def gmail_exchange(
    body: ExchangeIn,
    request: Request,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    stats = _sync_with_code(request, db, member, body.code, body.redirect_uri)
    return {"flights": stats.flights, "lodgings": stats.lodgings, "failures": stats.failures}


@router.get("/gmail/failures")
def gmail_failures(member: Member = Depends(get_member), db: Session = Depends(get_db)):
    rows = db.query(ParseFailure).filter_by(member_id=member.id).all()
    return [
        {
            "id": row.id,
            "gmail_message_id": row.gmail_message_id,
            "error": row.error,
            "can_retry": row.retries < 1 and bool(row.body),
        }
        for row in rows
    ]


@router.post("/gmail/failures/{failure_id}/retry")
def gmail_retry(
    failure_id: int,
    request: Request,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    failure = db.get(ParseFailure, failure_id)
    if failure is None or failure.member_id != member.id:
        raise HTTPException(status_code=404, detail="Nothing to retry")
    try:
        stats = retry_failure(db, failure, load_airports(), request.app.state.llm)
    except GmailError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"flights": stats.flights, "lodgings": stats.lodgings, "failures": stats.failures}


@router.post("/trips/assemble")
def assemble(member: Member = Depends(get_member), db: Session = Depends(get_db)):
    try:
        trips = assemble_member(db, member, load_airports())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "trips": [_trip_summary(db, trip) for trip in trips],
        "merge_suggestions": _pending_suggestions(db, member.household_id),
    }


@router.get("/trips")
def list_trips(member: Member = Depends(get_member), db: Session = Depends(get_db)):
    trips = (
        db.query(Trip)
        .filter_by(household_id=member.household_id)
        .order_by(Trip.start_date.desc())
        .all()
    )
    return [_trip_summary(db, trip) for trip in trips]


@router.get("/merge-suggestions")
def list_suggestions(member: Member = Depends(get_member), db: Session = Depends(get_db)):
    return _pending_suggestions(db, member.household_id)


@router.post("/merge-suggestions/{suggestion_id}/accept")
def accept_suggestion(
    suggestion_id: int,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    suggestion = _suggestion_for(db, member, suggestion_id)
    if suggestion.status == "dismissed":
        raise HTTPException(status_code=400, detail="That suggestion was dismissed")
    trip = accept_merge(db, suggestion, load_airports())
    return _trip_detail(db, trip)


@router.post("/merge-suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(
    suggestion_id: int,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    suggestion = _suggestion_for(db, member, suggestion_id)
    suggestion.status = "dismissed"
    db.commit()
    return {"status": "dismissed"}


@router.get("/trips/{trip_id}")
def get_trip(trip_id: int, member: Member = Depends(get_member), db: Session = Depends(get_db)):
    trip = _trip_for(db, member, trip_id)
    return _trip_detail(db, trip)


@router.patch("/trips/{trip_id}")
def patch_trip(
    trip_id: int,
    body: TripIn,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    trip = _trip_for(db, member, trip_id)
    trip.title = body.title.strip()
    trip.title_edited = True
    db.commit()
    return _trip_detail(db, trip)


@router.patch("/trips/{trip_id}/days/{day}")
def patch_day(
    trip_id: int,
    day: date,
    body: DayIn,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    trip = _trip_for(db, member, trip_id)
    row = db.query(JournalDay).filter_by(trip_id=trip.id, day=day).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="That day is not in the trip")
    row.body = body.body
    row.edited = True
    db.commit()
    return _trip_detail(db, trip)


@router.post("/trips/{trip_id}/days/{day}/photos")
def upload_thumbnail(
    trip_id: int,
    day: date,
    request: Request,
    file: UploadFile = File(...),
    asset_id: str = Form(...),
    taken_at: datetime = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    _check_coord(latitude, longitude)
    trip = _trip_for(db, member, trip_id)
    row = db.query(JournalDay).filter_by(trip_id=trip.id, day=day).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="That day is not in the trip")
    raw = file.file.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Thumbnail is too large")
    try:
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=80)
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Could not read that image") from None
    photo = db.query(JournalPhoto).filter_by(journal_day_id=row.id, asset_id=asset_id).one_or_none()
    if photo is None:
        photo = JournalPhoto(
            journal_day_id=row.id,
            member_id=member.id,
            asset_id=asset_id,
            taken_at=taken_at,
            latitude=latitude,
            longitude=longitude,
            selected=True,
            candidate=False,
        )
        db.add(photo)
        db.flush()
    else:
        photo.taken_at = taken_at
        photo.latitude = latitude
        photo.longitude = longitude
        photo.selected = True
    media_root = Path(request.app.state.settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    path = media_root / f"{photo.id}.jpg"
    path.write_bytes(buffer.getvalue())
    photo.thumbnail_path = str(path)
    db.commit()
    return _trip_detail(db, trip)


@router.delete("/trips/{trip_id}/photos/{photo_id}")
def hide_photo(
    trip_id: int,
    photo_id: int,
    member: Member = Depends(get_member),
    db: Session = Depends(get_db),
):
    trip = _trip_for(db, member, trip_id)
    photo = db.get(JournalPhoto, photo_id)
    if photo is None or photo.day.trip_id != trip.id:
        raise HTTPException(status_code=404, detail="Photo not found")
    photo.selected = False
    if photo.thumbnail_path:
        Path(photo.thumbnail_path).unlink(missing_ok=True)
        photo.thumbnail_path = ""
    db.commit()
    return _trip_detail(db, trip)


@router.get("/media/{photo_id}")
def media(photo_id: int, member: Member = Depends(get_member), db: Session = Depends(get_db)):
    photo = db.get(JournalPhoto, photo_id)
    if photo is None or not photo.thumbnail_path:
        raise HTTPException(status_code=404, detail="Photo not found")
    trip = photo.day.trip
    if trip.household_id != member.household_id:
        raise HTTPException(status_code=404, detail="Photo not found")
    path = Path(photo.thumbnail_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path, media_type="image/jpeg")


def _sync_with_code(request: Request, db: Session, member: Member, code: str, redirect_uri: str):
    gmail = request.app.state.gmail
    token = ""
    try:
        token = gmail.exchange_code(code, redirect_uri)
        messages = gmail.fetch_messages(token)
        return ingest_messages(db, member, messages, load_airports(), request.app.state.llm)
    except GmailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    finally:
        token = ""


def _login_member(db: Session, *, sub: str, email: str, display_name: str) -> Member:
    member = db.query(Member).filter_by(apple_sub=sub).one_or_none()
    if member is None:
        household = Household(name=f"{display_name or 'Family'}'s household", invite_code=_invite_code(db))
        db.add(household)
        db.flush()
        member = Member(
            household_id=household.id,
            apple_sub=sub,
            email=email,
            display_name=display_name,
        )
        db.add(member)
    else:
        if email:
            member.email = email
        if display_name:
            member.display_name = display_name
    db.commit()
    db.refresh(member)
    return member


def _invite_code(db: Session) -> str:
    for _ in range(5):
        code = secrets.token_hex(4)
        if db.query(Household).filter_by(invite_code=code).first() is None:
            return code
    return secrets.token_hex(8)


def _auth_payload(member: Member, request: Request) -> dict:
    token = create_session(member.id, request.app.state.settings.secret_key)
    return {"session_token": token, "member": _member_payload(member)}


def _member_payload(member: Member) -> dict:
    household = member.household
    home = None
    if member.home_latitude is not None and member.home_longitude is not None:
        home = {
            "city": member.home_city,
            "latitude": member.home_latitude,
            "longitude": member.home_longitude,
            "radius_km": member.home_radius_km,
            "airport_iata": member.home_airport_iata,
        }
    return {
        "id": member.id,
        "display_name": member.display_name,
        "email": member.email,
        "household_id": household.id,
        "household_name": household.name,
        "invite_code": household.invite_code,
        "home": home,
    }


def _trip_for(db: Session, member: Member, trip_id: int) -> Trip:
    trip = db.get(Trip, trip_id)
    if trip is None or trip.household_id != member.household_id:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def _suggestion_for(db: Session, member: Member, suggestion_id: int) -> MergeSuggestion:
    suggestion = db.get(MergeSuggestion, suggestion_id)
    if suggestion is None or suggestion.household_id != member.household_id:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return suggestion


def _trip_summary(db: Session, trip: Trip) -> dict:
    member_ids = sorted({source.member_id for source in trip.sources})
    return {
        "id": trip.id,
        "title": trip.title,
        "start_date": trip.start_date,
        "end_date": trip.end_date,
        "primary_city": trip.primary_city,
        "status": trip.status,
        "member_ids": member_ids,
    }


def _trip_detail(db: Session, trip: Trip) -> dict:
    airports = load_airports()
    member_ids = sorted({source.member_id for source in trip.sources})
    flights = db.query(Flight).filter(Flight.member_id.in_(member_ids or [-1])).all() if member_ids else []
    lodgings = db.query(Lodging).filter(Lodging.member_id.in_(member_ids or [-1])).all() if member_ids else []
    days = []
    for row in sorted(trip.days, key=lambda item: item.day):
        try:
            place_names = json.loads(row.place_names or "[]")
        except json.JSONDecodeError:
            place_names = []
        day_flights = [
            _flight_out(flight)
            for flight in flights
            if flight.departure_local_date == row.day
        ]
        day_lodgings = [
            {
                "name": lodging.name,
                "address": lodging.address,
                "check_in": lodging.check_in,
                "check_out": lodging.check_out,
                "confirmation_code": lodging.confirmation_code,
            }
            for lodging in lodgings
            if lodging.check_in_date == row.day
        ]
        days.append(
            {
                "day": row.day,
                "place_names": place_names,
                "body": row.body,
                "draft_body": row.draft_body,
                "edited": row.edited,
                "flights": day_flights,
                "lodgings": day_lodgings,
                "photos": [
                    {
                        "id": photo.id,
                        "asset_id": photo.asset_id,
                        "taken_at": photo.taken_at,
                        "latitude": photo.latitude,
                        "longitude": photo.longitude,
                        "thumbnail_url": f"/media/{photo.id}" if photo.thumbnail_path else None,
                        "selected": photo.selected,
                    }
                    for photo in row.photos
                    if photo.selected
                ],
            }
        )
    summary = _trip_summary(db, trip)
    summary.update({"route": trip_route(db, trip, airports), "days": days})
    return summary


def _flight_out(flight: Flight) -> dict:
    return {
        "airline": flight.airline,
        "flight_number": flight.flight_number,
        "origin_iata": flight.origin_iata,
        "destination_iata": flight.destination_iata,
        "departure_at": flight.departure_at,
        "arrival_at": flight.arrival_at,
        "confirmation_code": flight.confirmation_code,
    }


def _pending_suggestions(db: Session, household_id: int) -> list[dict]:
    rows = (
        db.query(MergeSuggestion)
        .filter_by(household_id=household_id, status="pending")
        .all()
    )
    payload = []
    for row in rows:
        left = db.get(Trip, row.trip_low_id)
        right = db.get(Trip, row.trip_high_id)
        if left is None or right is None:
            continue
        payload.append(
            {
                "id": row.id,
                "trip_ids": [row.trip_low_id, row.trip_high_id],
                "titles": [left.title, right.title],
            }
        )
    return payload
