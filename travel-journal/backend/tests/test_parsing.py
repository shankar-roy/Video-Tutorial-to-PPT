import json
from datetime import timezone

from app.gmail import gmail_query
from app.journal import draft_paragraph, format_title
from app.parsing import STRICT_PROMPT, Part, parse_message

AIRPORTS = {"SFO", "LIS", "CDG"}


def test_schema_org_beats_a_calendar_attachment():
    flight = {
        "@context": "https://schema.org",
        "@type": "FlightReservation",
        "reservationNumber": "RXJ34P",
        "reservationFor": {
            "@type": "Flight",
            "flightNumber": "900",
            "airline": {"@type": "Airline", "name": "United", "iataCode": "UA"},
            "departureAirport": {"@type": "Airport", "iataCode": "SFO"},
            "arrivalAirport": {"@type": "Airport", "iataCode": "LIS"},
            "departureTime": "2024-06-12T11:00:00-07:00",
            "arrivalTime": "2024-06-13T08:00:00+01:00",
        },
    }
    lodging = {
        "@type": "LodgingReservation",
        "reservationNumber": "H1",
        "checkinTime": "2024-06-13T15:00:00+01:00",
        "checkoutTime": "2024-06-18T11:00:00+01:00",
        "reservationFor": {
            "@type": ["LodgingBusiness", "LocalBusiness"],
            "name": "Hotel Bairro",
            "address": {"streetAddress": "Rua da Misericordia", "addressLocality": "Lisbon"},
        },
    }
    html = (
        "<html><script type=\"application/ld+json\">"
        + json.dumps({"@graph": [flight, lodging]})
        + "</script></html>"
    )
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Flight XX1 SFO to CDG
DTSTART:20240601T120000Z
DTEND:20240601T200000Z
END:VEVENT
END:VCALENDAR
"""
    result = parse_message(
        [Part("text/html", html), Part("text/calendar", ics, "trip.ics")],
        airports=AIRPORTS,
        llm=None,
    )
    assert result.ok
    assert len(result.flights) == 1
    assert result.flights[0].flight_number == "UA900"
    assert result.flights[0].origin_iata == "SFO"
    assert result.flights[0].destination_iata == "LIS"
    assert result.flights[0].confirmation_code == "RXJ34P"
    assert result.flights[0].departure_local_date.isoformat() == "2024-06-12"
    assert result.lodgings[0].name == "Hotel Bairro"
    assert "Lisbon" in result.lodgings[0].address
    assert result.lodgings[0].check_in_date.isoformat() == "2024-06-13"


def test_ics_when_there_is_no_schema():
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Flight TP1924 SFO to LIS
DTSTART:20240612T180000Z
DTEND:20240613T070000Z
LOCATION:SFO to LIS
END:VEVENT
BEGIN:VEVENT
SUMMARY:Hotel Bairro check-in
DTSTART;VALUE=DATE:20240613
DTEND;VALUE=DATE:20240618
LOCATION:Lisbon
END:VEVENT
END:VCALENDAR
"""
    result = parse_message([Part("text/calendar", ics, "trip.ics")], airports=AIRPORTS, llm=None)
    assert [flight.flight_number for flight in result.flights] == ["TP1924"]
    assert result.flights[0].departure_at.tzinfo == timezone.utc
    assert result.lodgings[0].name.startswith("Hotel Bairro")
    assert result.lodgings[0].check_in_date.isoformat() == "2024-06-13"


def test_prompt_runs_only_after_structured_parsers_fail():
    calls = []

    class FakeLLM:
        def complete(self, prompt: str) -> str:
            calls.append(prompt)
            assert prompt.startswith(STRICT_PROMPT)
            assert "SECRET_BODY_MARKER" in prompt
            return """```json
{"flights":[{"airline":"United","flight_number":"UA900","origin_iata":"SFO","destination_iata":"LIS","departure_at":"2024-06-12T18:00:00+00:00","arrival_at":"2024-06-13T07:00:00+00:00","confirmation_code":"ABC"}],"lodgings":[]}
```"""

    result = parse_message(
        [Part("text/plain", "Your trip is coming up. SECRET_BODY_MARKER")],
        airports=AIRPORTS,
        llm=FakeLLM(),
    )
    assert calls
    assert result.flights[0].confirmation_code == "ABC"
    assert result.flights[0].flight_number == "UA900"

    failed = parse_message(
        [Part("text/plain", "hello from a newsletter")],
        airports=AIRPORTS,
        llm=None,
    )
    assert not failed.ok
    assert failed.error == "no structured reservation found"


def test_gmail_query_covers_tickets_and_known_senders():
    query = gmail_query()
    assert "newer_than:730d" in query
    for term in ("flight", "itinerary", '"boarding pass"', "reservation", '"flight confirmation"'):
        assert term in query
    assert "from:united.com" in query
    assert "from:booking.com" in query


def test_title_and_paragraph_use_only_facts():
    assert format_title("Lisbon", _day(6, 12), _day(6, 18)) == "Lisbon, 12–18 Jun 2024"
    assert format_title("Paris", _day(6, 28), _day(7, 2)) == "Paris, 28 Jun–2 Jul 2024"
    flight = type(
        "Flight",
        (),
        {
            "airline": "United",
            "flight_number": "UA900",
            "origin_iata": "SFO",
            "destination_iata": "LIS",
            "departure_local_date": _day(6, 12),
            "arrival_local_date": _day(6, 13),
        },
    )()
    lodging = type("Lodging", (), {"name": "Hotel Bairro", "check_in_date": _day(6, 13)})()
    assert (
        draft_paragraph(_day(6, 12), [], [flight], [])
        == "Flew United UA900 from SFO to LIS."
    )
    assert "Arrived at LIS" in draft_paragraph(_day(6, 13), ["Lisbon"], [flight], [lodging])
    assert "Checked in at Hotel Bairro" in draft_paragraph(_day(6, 13), ["Lisbon"], [flight], [lodging])
    assert "still belongs to the trip" in draft_paragraph(_day(6, 16), [], [], [])


def _day(month: int, day: int):
    from datetime import date

    return date(2024, month, day)
