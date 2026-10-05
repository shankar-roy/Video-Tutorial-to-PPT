import io
import json
from datetime import datetime, timedelta, timezone

from PIL import Image

from app.assembly import Home, StayView, build_windows
from app.airports import load_airports
from app.gmail import RawMessage
from app.models import Flight, ParseFailure
from app.parsing import Part
from tests.conftest import auth_header, login

PDT = timezone(timedelta(hours=-7))
WEST = timezone(timedelta(hours=1))
UTC = timezone.utc
LISBON = (38.7139, -9.1393)
PARIS = (48.8566, 2.3522)


def test_apple_sign_in_creates_a_household_and_rejects_bad_tokens(client, sign_apple):
    first = login(client, sign_apple, "ava", "Ava")
    again = login(client, sign_apple, "ava", "Ava Stone")
    assert again["member"]["id"] == first["member"]["id"]
    assert again["member"]["display_name"] == "Ava Stone"
    assert again["member"]["invite_code"]

    bad = client.post("/auth/apple", json={"identity_token": "nope"})
    assert bad.status_code == 401
    wrong = client.post("/auth/apple", json={"identity_token": sign_apple("ava", audience="com.other.app")})
    assert wrong.status_code == 401
    assert client.post("/auth/dev", json={"sub": "ava"}).status_code == 404
    assert client.get("/me").status_code == 401
    assert client.get("/health").json() == {"status": "ok"}

    client.app.state.settings.allow_dev_login = True
    dev = client.post("/auth/dev", json={"sub": "local", "display_name": "Local"})
    assert dev.status_code == 200
    assert dev.json()["member"]["display_name"] == "Local"


def test_home_picks_the_nearest_airport(client, sign_apple):
    token = login(client, sign_apple, "ava", "Ava")["session_token"]
    response = client.put(
        "/me/home",
        headers=auth_header(token),
        json={"city": "San Francisco", "latitude": 37.7749, "longitude": -122.4194, "radius_km": 40},
    )
    assert response.status_code == 200, response.text
    assert response.json()["home"]["airport_iata"] == "SFO"
    missing = client.post("/trips/assemble", headers=auth_header(login(client, sign_apple, "nohome", "No")["session_token"]))
    assert missing.status_code == 400


def test_gmail_sync_discards_the_token_and_keeps_a_failure_for_one_retry(client, sign_apple):
    token = login(client, sign_apple, "ava", "Ava")["session_token"]
    start = client.get("/gmail/start", headers=auth_header(token))
    assert start.status_code == 200, start.text
    url = start.json()["authorization_url"]
    assert "access_type=online" in url
    assert "access_type=offline" not in url
    assert "gmail.readonly" in url
    assert "prompt=consent" in url

    seen = {}

    def exchange(code, redirect_uri):
        seen["code"] = code
        seen["redirect"] = redirect_uri
        return "TOKEN_SENTINEL"

    def fetch(_access_token):
        seen["fetched"] = True
        return [
            RawMessage(id="m-ok", parts=[Part("text/html", _ticket_html("RXJ34P"))]),
            RawMessage(id="m-bad", parts=[Part("text/plain", "newsletter SECRET_BODY_MARKER")]),
        ]

    client.app.state.gmail.exchange_code = exchange
    client.app.state.gmail.fetch_messages = fetch
    synced = client.post(
        "/gmail/exchange",
        headers=auth_header(token),
        json={"code": "auth-code", "redirect_uri": "http://127.0.0.1:8000/gmail/callback"},
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["flights"] == 2
    assert synced.json()["failures"] == 1
    assert seen["code"] == "auth-code"

    text = _dump(client)
    assert "TOKEN_SENTINEL" not in text
    assert "SECRET_BODY_MARKER" in text
    assert "RXJ34P" in text

    synced_again = client.post(
        "/gmail/exchange",
        headers=auth_header(token),
        json={"code": "auth-code", "redirect_uri": "http://127.0.0.1:8000/gmail/callback"},
    )
    assert synced_again.status_code == 200
    db = client.app.state.session_factory()
    try:
        assert db.query(Flight).count() == 2
    finally:
        db.close()

    failures = client.get("/gmail/failures", headers=auth_header(token)).json()
    assert failures[0]["can_retry"] is True

    class Later:
        def complete(self, _prompt: str) -> str:
            return json.dumps(
                {
                    "flights": [
                        {
                            "airline": "TAP",
                            "flight_number": "TP1924",
                            "origin_iata": "SFO",
                            "destination_iata": "LIS",
                            "departure_at": "2024-07-01T18:00:00+00:00",
                            "arrival_at": "2024-07-02T07:00:00+00:00",
                            "confirmation_code": "RETRY1",
                        }
                    ],
                    "lodgings": [],
                }
            )

    client.app.state.llm = Later()
    retried = client.post(f"/gmail/failures/{failures[0]['id']}/retry", headers=auth_header(token))
    assert retried.status_code == 200, retried.text
    assert "SECRET_BODY_MARKER" not in _dump(client)
    assert "RETRY1" in _dump(client)
    again = client.post(f"/gmail/failures/{failures[0]['id']}/retry", headers=auth_header(token))
    assert again.status_code == 404

    state = url.split("state=")[1].split("&")[0]
    from urllib.parse import unquote

    callback = client.get(
        "/gmail/callback",
        params={"code": "auth-code", "state": unquote(state)},
        follow_redirects=False,
    )
    assert callback.status_code == 307
    assert callback.headers["location"].startswith("traveljournal://gmail-synced?")


def test_a_failed_retry_cannot_be_repeated(client, sign_apple):
    token = login(client, sign_apple, "retry", "Retry")["session_token"]

    def exchange(_code, _redirect):
        return "TOKEN_SENTINEL"

    def fetch(_token):
        return [RawMessage(id="m-plain", parts=[Part("text/plain", "no reservation here BODY_ONCE")])]

    client.app.state.gmail.exchange_code = exchange
    client.app.state.gmail.fetch_messages = fetch

    class Empty:
        def complete(self, _prompt: str) -> str:
            return '{"flights":[],"lodgings":[]}'

    client.app.state.llm = Empty()
    client.post(
        "/gmail/exchange",
        headers=auth_header(token),
        json={"code": "c", "redirect_uri": "http://127.0.0.1:8000/gmail/callback"},
    )
    failure = client.get("/gmail/failures", headers=auth_header(token)).json()[0]
    first = client.post(f"/gmail/failures/{failure['id']}/retry", headers=auth_header(token))
    assert first.status_code == 200, first.text
    assert "BODY_ONCE" not in _dump(client)
    second = client.post(f"/gmail/failures/{failure['id']}/retry", headers=auth_header(token))
    assert second.status_code == 400


def test_assembles_a_lisbon_journal_and_merges_household_members(client, sign_apple):
    ava = _prepare_traveler(client, sign_apple, "ava", "Ava")
    noah = _prepare_traveler(client, sign_apple, "noah", "Noah")
    joined = client.post(
        "/households/join",
        headers=auth_header(noah["token"]),
        json={"invite_code": ava["invite_code"]},
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["household_id"] == ava["member"]["household_id"]
    assert client.post("/households/join", headers=auth_header(noah["token"]), json={"invite_code": "missing"}).status_code == 404

    built = client.post("/trips/assemble", headers=auth_header(ava["token"]))
    assert built.status_code == 200, built.text
    assert len(built.json()["trips"]) == 1
    again = client.post("/trips/assemble", headers=auth_header(ava["token"]))
    assert len(again.json()["trips"]) == 1
    assert len(client.get("/trips", headers=auth_header(ava["token"])).json()) == 1

    trip_id = built.json()["trips"][0]["id"]
    detail = client.get(f"/trips/{trip_id}", headers=auth_header(ava["token"])).json()
    assert detail["title"] == "Lisbon, 12–18 Jun 2024"
    assert detail["primary_city"] == "Lisbon"
    days = {row["day"]: row for row in detail["days"]}
    assert "2024-06-16" in days
    assert "still belongs to the trip" in days["2024-06-16"]["body"]
    assert "Flew United UA900 from SFO to LIS" in days["2024-06-12"]["body"]
    assert "Hotel Bairro" in days["2024-06-13"]["body"]
    assert "Lisbon" in days["2024-06-13"]["place_names"]
    assert days["2024-06-13"]["photos"]
    assert all(row["day"] < "2024-09-01" for row in detail["days"])
    assert any(point["name"] == "LIS" and point["kind"] == "airport" for point in detail["route"])
    assert detail["route"][0]["kind"] == "home"
    assert detail["route"][-1]["kind"] == "home"

    saved = client.patch(
        f"/trips/{trip_id}/days/2024-06-13",
        headers=auth_header(ava["token"]),
        json={"body": "We got lost in Alfama."},
    )
    assert saved.json()["days"][1]["edited"] is True or any(
        row["body"] == "We got lost in Alfama." and row["edited"] for row in saved.json()["days"]
    )
    client.post("/trips/assemble", headers=auth_header(ava["token"]))
    kept = client.get(f"/trips/{trip_id}", headers=auth_header(ava["token"])).json()
    june_13 = next(row for row in kept["days"] if row["day"] == "2024-06-13")
    assert june_13["body"] == "We got lost in Alfama."
    assert june_13["edited"] is True

    client.post("/trips/assemble", headers=auth_header(noah["token"]))
    suggestions = client.get("/merge-suggestions", headers=auth_header(ava["token"])).json()
    assert len(suggestions) == 1
    merged = client.post(
        f"/merge-suggestions/{suggestions[0]['id']}/accept",
        headers=auth_header(ava["token"]),
    )
    assert merged.status_code == 200, merged.text
    assert sorted(merged.json()["member_ids"]) == sorted([ava["member"]["id"], noah["member"]["id"]])
    assert len(client.get("/trips", headers=auth_header(ava["token"])).json()) == 1

    outsider = login(client, sign_apple, "outsider", "Out")
    assert client.get(f"/trips/{trip_id}", headers=auth_header(outsider["session_token"])).status_code == 404

    image = io.BytesIO()
    Image.new("RGB", (20, 10), "blue").save(image, format="JPEG")
    uploaded = client.post(
        f"/trips/{merged.json()['id']}/days/2024-06-14/photos",
        headers=auth_header(ava["token"]),
        data={
            "asset_id": "shared-frame",
            "taken_at": "2024-06-14T12:00:00+01:00",
            "latitude": str(LISBON[0]),
            "longitude": str(LISBON[1]),
        },
        files={"file": ("frame.jpg", image.getvalue(), "image/jpeg")},
    )
    assert uploaded.status_code == 200, uploaded.text
    photos = next(row["photos"] for row in uploaded.json()["days"] if row["day"] == "2024-06-14")
    shared = next(photo for photo in photos if photo["asset_id"] == "shared-frame")
    assert shared["thumbnail_url"]
    fetched = client.get(shared["thumbnail_url"], headers=auth_header(noah["token"]))
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/jpeg"
    hidden = client.get(shared["thumbnail_url"], headers=auth_header(outsider["session_token"]))
    assert hidden.status_code == 404


def test_dismissed_merge_is_not_suggested_again(client, sign_apple):
    ava = _prepare_traveler(client, sign_apple, "ava2", "Ava")
    noah = _prepare_traveler(client, sign_apple, "noah2", "Noah")
    client.post("/households/join", headers=auth_header(noah["token"]), json={"invite_code": ava["invite_code"]})
    client.post("/trips/assemble", headers=auth_header(ava["token"]))
    client.post("/trips/assemble", headers=auth_header(noah["token"]))
    suggestion = client.get("/merge-suggestions", headers=auth_header(ava["token"])).json()[0]
    assert client.post(
        f"/merge-suggestions/{suggestion['id']}/dismiss",
        headers=auth_header(ava["token"]),
    ).status_code == 200
    client.post("/trips/assemble", headers=auth_header(ava["token"]))
    assert client.get("/merge-suggestions", headers=auth_header(ava["token"])).json() == []


def test_photo_excursion_without_tickets_becomes_a_trip():
    home = Home(37.7749, -122.4194, 40, "San Francisco", "SFO")
    start = datetime(2024, 8, 1, 10, tzinfo=UTC)
    end = start + timedelta(hours=25)
    short_end = datetime(2024, 9, 2, 14, tzinfo=UTC)
    stays = [
        StayView(1, start, end, start.date(), end.date(), *LISBON, "Lisbon", 2, []),
        StayView(2, datetime(2024, 9, 2, 12, tzinfo=UTC), short_end, short_end.date(), short_end.date(), *PARIS, "Paris", 1, []),
    ]
    windows = build_windows(home, [], stays, load_airports())
    assert len(windows) == 1
    assert windows[0].city == "Lisbon"
    assert windows[0].end_at - windows[0].start_at > timedelta(hours=24)


def _prepare_traveler(client, sign_apple, sub: str, name: str) -> dict:
    signed = login(client, sign_apple, sub, name)
    token = signed["session_token"]
    home = client.put(
        "/me/home",
        headers=auth_header(token),
        json={"city": "San Francisco", "latitude": 37.7749, "longitude": -122.4194, "radius_km": 40},
    )
    assert home.status_code == 200, home.text

    def exchange(_code, _redirect):
        return "TOKEN_SENTINEL"

    def fetch(_token):
        return [RawMessage(id=f"{sub}-mail", parts=[Part("text/html", _ticket_html("CONF" + sub))])]

    client.app.state.gmail.exchange_code = exchange
    client.app.state.gmail.fetch_messages = fetch
    synced = client.post(
        "/gmail/exchange",
        headers=auth_header(token),
        json={"code": "c", "redirect_uri": "http://127.0.0.1:8000/gmail/callback"},
    )
    assert synced.status_code == 200, synced.text
    stays = client.post("/me/stays", headers=auth_header(token), json={"stays": _lisbon_stays(sub)})
    assert stays.status_code == 200, stays.text
    signed["token"] = token
    signed["invite_code"] = signed["member"]["invite_code"]
    return signed


def _lisbon_stays(sub: str) -> list[dict]:
    def shot(asset: str, when: datetime):
        return {
            "asset_id": f"{sub}-{asset}",
            "taken_at": when.isoformat(),
            "latitude": LISBON[0],
            "longitude": LISBON[1],
        }

    def stay(key: str, start: datetime, end: datetime, photos: list[dict]):
        return {
            "client_id": f"{sub}-{key}",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "latitude": LISBON[0],
            "longitude": LISBON[1],
            "place_name": "Lisbon",
            "photos": photos,
        }

    paris_start = datetime(2024, 9, 2, 12, tzinfo=UTC)
    return [
        stay(
            "jun13",
            datetime(2024, 6, 13, 18, tzinfo=WEST),
            datetime(2024, 6, 13, 23, tzinfo=WEST),
            [
                shot("day", datetime(2024, 6, 13, 18, tzinfo=WEST)),
                shot("night", datetime(2024, 6, 13, 23, tzinfo=WEST)),
            ],
        ),
        stay(
            "jun14",
            datetime(2024, 6, 14, 12, tzinfo=WEST),
            datetime(2024, 6, 14, 23, 30, tzinfo=WEST),
            [shot("14", datetime(2024, 6, 14, 12, tzinfo=WEST))],
        ),
        stay(
            "jun15",
            datetime(2024, 6, 15, 12, tzinfo=WEST),
            datetime(2024, 6, 15, 16, tzinfo=WEST),
            [shot("15", datetime(2024, 6, 15, 12, tzinfo=WEST))],
        ),
        stay(
            "jun17",
            datetime(2024, 6, 17, 12, tzinfo=WEST),
            datetime(2024, 6, 17, 16, tzinfo=WEST),
            [shot("17", datetime(2024, 6, 17, 12, tzinfo=WEST))],
        ),
        {
            "client_id": f"{sub}-paris",
            "start_at": paris_start.isoformat(),
            "end_at": (paris_start + timedelta(hours=2)).isoformat(),
            "latitude": PARIS[0],
            "longitude": PARIS[1],
            "place_name": "Paris",
            "photos": [
                {
                    "asset_id": f"{sub}-paris",
                    "taken_at": paris_start.isoformat(),
                    "latitude": PARIS[0],
                    "longitude": PARIS[1],
                }
            ],
        },
    ]


def _ticket_html(code: str) -> str:
    outbound = _flight("UA", "United", "900", "SFO", "LIS", "2024-06-12T11:00:00-07:00", "2024-06-13T08:00:00+01:00", code)
    inbound = _flight("UA", "United", "901", "LIS", "SFO", "2024-06-18T12:00:00+01:00", "2024-06-18T15:00:00-07:00", code)
    hotel = {
        "@type": "LodgingReservation",
        "reservationNumber": code,
        "checkinTime": "2024-06-13T15:00:00+01:00",
        "checkoutTime": "2024-06-18T11:00:00+01:00",
        "reservationFor": {"@type": "LodgingBusiness", "name": "Hotel Bairro", "address": "Lisbon"},
    }
    payload = json.dumps({"@graph": [outbound, inbound, hotel]})
    return f'<html><script type="application/ld+json">{payload}</script></html>'


def _flight(iata, airline, number, origin, destination, depart, arrive, code):
    return {
        "@type": "FlightReservation",
        "reservationNumber": code,
        "reservationFor": {
            "@type": "Flight",
            "flightNumber": number,
            "airline": {"name": airline, "iataCode": iata},
            "departureAirport": {"iataCode": origin},
            "arrivalAirport": {"iataCode": destination},
            "departureTime": depart,
            "arrivalTime": arrive,
        },
    }


def _dump(client) -> str:
    db = client.app.state.session_factory()
    try:
        chunks = []
        for table in Flight.metadata.sorted_tables:
            for row in db.execute(table.select()):
                chunks.append(" ".join("" if value is None else str(value) for value in row))
        failures = db.query(ParseFailure).count()
        chunks.append(f"failures={failures}")
        return "\n".join(chunks)
    finally:
        db.close()
