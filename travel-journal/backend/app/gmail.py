"""On-demand Gmail sync. The access token is used for this request and discarded."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from app.airports import Airports
from app.models import Flight, Lodging, ParseFailure
from app.parsing import STRICT_PROMPT, Completer, Part, parse_message
from app.settings import Settings

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SENDERS = (
    "united.com",
    "delta.com",
    "aa.com",
    "southwest.com",
    "jetblue.com",
    "alaskaair.com",
    "britishairways.com",
    "lufthansa.com",
    "airfrance.fr",
    "klm.com",
    "ryanair.com",
    "easyjet.com",
    "booking.com",
    "airbnb.com",
    "expedia.com",
    "hotels.com",
    "marriott.com",
    "hilton.com",
)


class GmailError(Exception):
    pass


@dataclass
class RawMessage:
    id: str
    parts: list[Part] = field(default_factory=list)


@dataclass
class SyncStats:
    flights: int = 0
    lodgings: int = 0
    failures: int = 0


def gmail_query() -> str:
    terms = [
        "flight",
        "itinerary",
        '"boarding pass"',
        "reservation",
        '"flight confirmation"',
        *[f"from:{sender}" for sender in SENDERS],
    ]
    return "newer_than:730d (" + " OR ".join(terms) + ")"


class GmailService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return bool(self.settings.google_client_id and self.settings.google_client_secret)

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_SCOPE,
            "access_type": "online",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": state,
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def exchange_code(self, code: str, redirect_uri: str) -> str:
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise GmailError("Google did not return an access token")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise GmailError("Google did not return an access token")
        # A refresh token is ignored on purpose. Sync stays on demand.
        return token

    def fetch_messages(self, access_token: str) -> list[RawMessage]:
        headers = {"Authorization": f"Bearer {access_token}"}
        identifiers: list[str] = []
        page = None
        limit = self.settings.gmail_message_limit
        while len(identifiers) < limit:
            params = {"q": gmail_query(), "maxResults": min(50, limit - len(identifiers))}
            if page:
                params["pageToken"] = page
            response = httpx.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params=params,
                timeout=30,
            )
            if response.status_code == 401:
                raise GmailError("Gmail authorization expired; sync again")
            if response.status_code != 200:
                raise GmailError("Gmail search failed")
            payload = response.json()
            identifiers.extend(item["id"] for item in payload.get("messages") or [])
            page = payload.get("nextPageToken")
            if not page:
                break
        messages = []
        for message_id in identifiers:
            response = httpx.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers=headers,
                params={"format": "full"},
                timeout=30,
            )
            if response.status_code != 200:
                raise GmailError("Gmail message fetch failed")
            messages.append(
                RawMessage(id=message_id, parts=flatten_payload(response.json().get("payload") or {}))
            )
        return messages


def flatten_payload(payload: dict) -> list[Part]:
    parts: list[Part] = []

    def walk(node: dict) -> None:
        body = node.get("body") or {}
        data = body.get("data")
        if data:
            padded = data + "=" * (-len(data) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            parts.append(
                Part(
                    mime=node.get("mimeType") or "text/plain",
                    body=raw.decode("utf-8", errors="replace"),
                    filename=node.get("filename") or "",
                )
            )
        for child in node.get("parts") or []:
            walk(child)

    walk(payload)
    return parts


def ingest_messages(db, member, messages: list[RawMessage], airports: Airports, llm: Completer | None) -> SyncStats:
    stats = SyncStats()
    known = airports.codes()
    for message in messages:
        result = parse_message(message.parts, airports=known, llm=llm)
        if result.ok:
            for flight in result.flights:
                _upsert_flight(db, member.id, message.id, flight, airports)
                stats.flights += 1
            for lodging in result.lodgings:
                _upsert_lodging(db, member.id, message.id, lodging)
                stats.lodgings += 1
            failure = (
                db.query(ParseFailure)
                .filter_by(member_id=member.id, gmail_message_id=message.id)
                .one_or_none()
            )
            if failure is not None:
                db.delete(failure)
            continue
        plain = "\n".join(part.body for part in message.parts if part.body)[:20000]
        failure = (
            db.query(ParseFailure)
            .filter_by(member_id=member.id, gmail_message_id=message.id)
            .one_or_none()
        )
        if failure is None:
            failure = ParseFailure(member_id=member.id, gmail_message_id=message.id)
            db.add(failure)
        failure.error = (result.error or "no structured reservation found")[:300]
        failure.body = plain
        failure.retries = 0
        stats.failures += 1
    db.commit()
    return stats


def retry_failure(db, failure: ParseFailure, airports: Airports, llm: Completer | None) -> SyncStats:
    """Re-parse a stored failure once, then drop the email body either way."""
    if failure.retries >= 1 or not failure.body:
        raise GmailError("That email was already retried")
    body = failure.body
    message_id = failure.gmail_message_id
    member_id = failure.member_id
    result = parse_message([Part("text/plain", body)], airports=airports.codes(), llm=llm)
    failure.retries = 1
    failure.body = ""
    stats = SyncStats()
    if result.ok:
        for flight in result.flights:
            _upsert_flight(db, member_id, message_id, flight, airports)
            stats.flights += 1
        for lodging in result.lodgings:
            _upsert_lodging(db, member_id, message_id, lodging)
            stats.lodgings += 1
        db.delete(failure)
    else:
        failure.error = (result.error or "no structured reservation found")[:300]
    db.commit()
    return stats


def _upsert_flight(db, member_id: int, message_id: str, flight, airports: Airports) -> None:
    origin = airports.get(flight.origin_iata)
    destination = airports.get(flight.destination_iata)
    row = (
        db.query(Flight)
        .filter_by(
            member_id=member_id,
            gmail_message_id=message_id,
            flight_number=flight.flight_number,
            departure_at=flight.departure_at,
        )
        .one_or_none()
    )
    if row is None:
        row = Flight(
            member_id=member_id,
            gmail_message_id=message_id,
            flight_number=flight.flight_number,
            departure_at=flight.departure_at,
        )
        db.add(row)
    row.airline = flight.airline
    row.origin_iata = flight.origin_iata
    row.destination_iata = flight.destination_iata
    row.arrival_at = flight.arrival_at
    row.departure_local_date = flight.departure_local_date
    row.arrival_local_date = flight.arrival_local_date
    row.confirmation_code = flight.confirmation_code
    if origin:
        row.origin_lat = origin["lat"]
        row.origin_lon = origin["lon"]
    if destination:
        row.destination_lat = destination["lat"]
        row.destination_lon = destination["lon"]


def _upsert_lodging(db, member_id: int, message_id: str, lodging) -> None:
    row = (
        db.query(Lodging)
        .filter_by(
            member_id=member_id,
            gmail_message_id=message_id,
            name=lodging.name,
            check_in=lodging.check_in,
        )
        .one_or_none()
    )
    if row is None:
        row = Lodging(
            member_id=member_id,
            gmail_message_id=message_id,
            name=lodging.name,
            check_in=lodging.check_in,
        )
        db.add(row)
    row.address = lodging.address
    row.check_out = lodging.check_out
    row.check_in_date = lodging.check_in_date
    row.check_out_date = lodging.check_out_date
    row.confirmation_code = lodging.confirmation_code


def prompt_text() -> str:
    return STRICT_PROMPT
