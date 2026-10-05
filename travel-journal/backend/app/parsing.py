"""Pull flights and hotels out of confirmation mail.

Order is fixed: schema.org JSON-LD, then calendar attachments, then a strict
JSON prompt. The prompt runs only when the first two find nothing, and only
on that one email's text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import Protocol

IATA = re.compile(r"^[A-Z]{3}$")
FLIGHT_NUMBER = re.compile(r"\b([A-Z]{2})\s?(\d{1,4})\b")
AIRPORT_PAIR = re.compile(r"\b([A-Za-z]{3})\s*(?:to|-|–|—)\s*([A-Za-z]{3})\b", re.I)

STRICT_PROMPT = """Extract travel reservations from the email. Reply with JSON only, no markdown, in this shape:
{"flights":[{"airline":"","flight_number":"","origin_iata":"","destination_iata":"","departure_at":"","arrival_at":"","confirmation_code":""}],"lodgings":[{"name":"","address":"","check_in":"","check_out":"","confirmation_code":""}]}
Use ISO-8601 for times. Use empty arrays if none. Do not include any other text.
EMAIL:
"""


@dataclass
class Part:
    mime: str
    body: str
    filename: str = ""


@dataclass
class ParsedFlight:
    airline: str
    flight_number: str
    origin_iata: str
    destination_iata: str
    departure_at: datetime
    arrival_at: datetime | None
    confirmation_code: str
    departure_local_date: object
    arrival_local_date: object | None


@dataclass
class ParsedLodging:
    name: str
    address: str
    check_in: datetime
    check_out: datetime | None
    confirmation_code: str
    check_in_date: object
    check_out_date: object | None


@dataclass
class ParseResult:
    flights: list[ParsedFlight] = field(default_factory=list)
    lodgings: list[ParsedLodging] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.flights or self.lodgings)


class Completer(Protocol):
    def complete(self, prompt: str) -> str: ...


def parse_message(parts: list[Part], *, airports: set[str], llm: Completer | None) -> ParseResult:
    text = "\n".join(part.body for part in parts if part.body)
    structured = parse_schema_org(text)
    if structured.ok:
        return structured

    ics_blobs = [
        part.body
        for part in parts
        if "calendar" in part.mime.lower() or part.filename.lower().endswith(".ics")
    ]
    if not ics_blobs and "BEGIN:VCALENDAR" in text:
        ics_blobs = [text]
    if ics_blobs:
        calendar = parse_ics("\n".join(ics_blobs), airports)
        if calendar.ok:
            return calendar

    if llm is None:
        return ParseResult(error="no structured reservation found")
    plain = strip_html(text)[:12000]
    try:
        extracted = parse_llm_payload(llm.complete(STRICT_PROMPT + plain))
    except (ValueError, json.JSONDecodeError) as exc:
        return ParseResult(error=f"unstructured email: {exc}")
    if extracted.ok:
        return extracted
    return ParseResult(error="no structured reservation found")


def parse_schema_org(text: str) -> ParseResult:
    result = ParseResult()
    for node in _json_blobs(text):
        _walk(node, result)
    return result


def parse_ics(text: str, airports: set[str]) -> ParseResult:
    result = ParseResult()
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, flags=re.S):
        fields = _ics_fields(block)
        summary = fields.get("SUMMARY", ("", ""))[1]
        location = fields.get("LOCATION", ("", ""))[1]
        description = fields.get("DESCRIPTION", ("", ""))[1]
        start = _ics_time(*fields["DTSTART"]) if "DTSTART" in fields else None
        end = _ics_time(*fields["DTEND"]) if "DTEND" in fields else None
        if start is None:
            continue
        blob = " ".join((summary, location, description))
        number = FLIGHT_NUMBER.search(blob.upper())
        pair = AIRPORT_PAIR.search(blob)
        origin = pair.group(1).upper() if pair else ""
        destination = pair.group(2).upper() if pair else ""
        if airports:
            if origin not in airports:
                origin = ""
            if destination not in airports:
                destination = ""
        if number and origin and destination:
            code = number.group(1) + number.group(2)
            result.flights.append(
                _flight(
                    airline=number.group(1),
                    flight_number=code,
                    origin=origin,
                    destination=destination,
                    departure=start,
                    arrival=end,
                    confirmation="",
                )
            )
            continue
        if re.search(r"hotel|airbnb|lodging|check-?in", summary, re.I):
            result.lodgings.append(
                _lodging(
                    name=summary.strip() or "Hotel",
                    address=location.strip(),
                    check_in=start,
                    check_out=end,
                    confirmation="",
                )
            )
    return result


def parse_llm_payload(raw: str) -> ParseResult:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no json")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(data, dict):
        raise ValueError("expected object")
    result = ParseResult()
    for item in data.get("flights") or []:
        if not isinstance(item, dict):
            continue
        departure = _parse_time(item.get("departure_at"))
        origin = _iata(item.get("origin_iata"))
        destination = _iata(item.get("destination_iata"))
        if not (departure and origin and destination):
            continue
        airline = str(item.get("airline") or "").strip()
        number = _normalize_flight_number(airline, str(item.get("flight_number") or ""))
        result.flights.append(
            _flight(
                airline=airline,
                flight_number=number or str(item.get("flight_number") or "").strip(),
                origin=origin,
                destination=destination,
                departure=departure,
                arrival=_parse_time(item.get("arrival_at")),
                confirmation=str(item.get("confirmation_code") or "").strip(),
            )
        )
    for item in data.get("lodgings") or []:
        if not isinstance(item, dict):
            continue
        check_in = _parse_time(item.get("check_in"))
        name = str(item.get("name") or "").strip()
        if not (check_in and name):
            continue
        result.lodgings.append(
            _lodging(
                name=name,
                address=str(item.get("address") or "").strip(),
                check_in=check_in,
                check_out=_parse_time(item.get("check_out")),
                confirmation=str(item.get("confirmation_code") or "").strip(),
            )
        )
    return result


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _json_blobs(text: str) -> list:
    blobs = []
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        flags=re.I | re.S,
    ):
        raw = unescape(match.group(1).strip())
        try:
            blobs.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    if blobs:
        found = ParseResult()
        for node in blobs:
            _walk(node, found)
        if found.ok:
            return blobs
    decoder = json.JSONDecoder()
    index = 0
    attempts = 0
    while attempts < 200 and len(blobs) < 50:
        start = text.find("{", index)
        if start < 0:
            break
        attempts += 1
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        blobs.append(obj)
        index = start + end
    return blobs


def _walk(node, result: ParseResult) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, result)
        return
    if not isinstance(node, dict):
        return
    types = _types(node.get("@type"))
    if "FlightReservation" in types:
        result.flights.extend(_flights_from_reservation(node))
    elif "LodgingReservation" in types:
        lodging = _lodging_from_reservation(node)
        if lodging is not None:
            result.lodgings.append(lodging)
    for key in ("@graph", "itemListElement"):
        if key in node:
            _walk(node[key], result)


def _flights_from_reservation(node: dict) -> list[ParsedFlight]:
    confirmation = str(node.get("reservationNumber") or node.get("reservationId") or "").strip()
    flights = []
    for flight in _as_list(node.get("reservationFor")):
        if not isinstance(flight, dict):
            continue
        airline_node = flight.get("airline") or {}
        if isinstance(airline_node, list):
            airline_node = airline_node[0] if airline_node else {}
        airline = ""
        airline_iata = ""
        if isinstance(airline_node, dict):
            airline = str(airline_node.get("name") or "").strip()
            airline_iata = str(airline_node.get("iataCode") or "").strip()
        number = _normalize_flight_number(airline_iata, str(flight.get("flightNumber") or ""))
        origin = _airport_code(flight.get("departureAirport"))
        destination = _airport_code(flight.get("arrivalAirport"))
        departure = _parse_time(flight.get("departureTime") or flight.get("departure_at"))
        arrival = _parse_time(flight.get("arrivalTime") or flight.get("arrival_at"))
        if origin and destination and departure and number:
            flights.append(
                _flight(
                    airline=airline or airline_iata,
                    flight_number=number,
                    origin=origin,
                    destination=destination,
                    departure=departure,
                    arrival=arrival,
                    confirmation=confirmation,
                )
            )
    return flights


def _lodging_from_reservation(node: dict) -> ParsedLodging | None:
    business = node.get("reservationFor") or {}
    if isinstance(business, list):
        business = business[0] if business else {}
    if not isinstance(business, dict):
        return None
    name = str(business.get("name") or "").strip()
    check_in = _parse_time(node.get("checkinTime") or node.get("checkinDate"))
    if not name or check_in is None:
        return None
    return _lodging(
        name=name,
        address=_format_address(business.get("address")),
        check_in=check_in,
        check_out=_parse_time(node.get("checkoutTime") or node.get("checkoutDate")),
        confirmation=str(node.get("reservationNumber") or "").strip(),
    )


def _flight(*, airline, flight_number, origin, destination, departure, arrival, confirmation) -> ParsedFlight:
    return ParsedFlight(
        airline=airline,
        flight_number=flight_number,
        origin_iata=origin,
        destination_iata=destination,
        departure_at=departure,
        arrival_at=arrival,
        confirmation_code=confirmation,
        departure_local_date=departure.date(),
        arrival_local_date=arrival.date() if arrival else None,
    )


def _lodging(*, name, address, check_in, check_out, confirmation) -> ParsedLodging:
    return ParsedLodging(
        name=name,
        address=address,
        check_in=check_in,
        check_out=check_out,
        confirmation_code=confirmation,
        check_in_date=check_in.date(),
        check_out_date=check_out.date() if check_out else None,
    )


def _normalize_flight_number(airline_iata: str, number: str) -> str:
    compact = re.sub(r"\s+", "", number).upper()
    code = re.sub(r"[^A-Z0-9]", "", airline_iata).upper()
    if code and compact.isdigit():
        return code + compact
    return compact


def _airport_code(node) -> str:
    if isinstance(node, list):
        node = node[0] if node else None
    if not isinstance(node, dict):
        return ""
    return _iata(node.get("iataCode") or node.get("iata"))


def _iata(value) -> str:
    code = str(value or "").strip().upper()
    return code if IATA.fullmatch(code) else ""


def _types(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _format_address(address) -> str:
    if isinstance(address, str):
        return address.strip()
    if isinstance(address, dict):
        parts = [
            address.get("streetAddress"),
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        return ", ".join(str(part) for part in parts if part)
    return ""


def _parse_time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed


def _ics_fields(block: str) -> dict[str, tuple[str, str]]:
    fields: dict[str, tuple[str, str]] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        key, _, params = left.partition(";")
        fields[key.strip().upper()] = (params, value.strip())
    return fields


def _ics_time(params: str, value: str) -> datetime | None:
    if re.fullmatch(r"\d{8}", value) or "VALUE=DATE" in params and "DATE-TIME" not in params and re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if re.fullmatch(r"\d{8}T\d{6}", value):
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    return None
