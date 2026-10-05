# Travel Journal

A private family journal. The iPhone reads photo locations and turns them into stays. A small API reads Gmail flight and hotel mail when someone taps Sync, then drafts a day-by-day trip with a map. People edit the draft. Nothing is published on its own.

Original photos stay in the Photos library. The phone uploads timestamps, coordinates, and only the thumbnails someone includes in an entry.

## Layout

- `ios/TravelJournal` — SwiftUI app (PhotoKit, MapKit, Sign in with Apple, SwiftData cache)
- `backend` — FastAPI service. Postgres in production, SQLite for tests.

Photo clustering rules live in both `ios/TravelJournal/TravelJournal/StayClusterer.swift` and `backend/app/clustering.py`. A new stay starts when the gap since the previous located photo is over 6 hours, or the move is over 30 km. Home is the cluster within 50 km that has the most nights (22:00 that date, or before 06:00 counting as the previous date). The person confirms the city and radius.

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Tests:

```bash
cd backend
python3 -m pytest
```

Postgres:

```bash
docker compose up -d
```

Then set `DATABASE_URL=postgresql+psycopg://travel:travel@localhost:5432/traveljournal`.

Airport coordinates are a filtered OurAirports extract (`backend/data/airports.json`). OurAirports data is public domain.

### Gmail

Sync is on demand. The app opens Google, the API exchanges the code for a short-lived access token, parses matching mail, and discards the token. It does not store a refresh token. `gmail.readonly` is a restricted scope; a private family project should stay out of Google's verification review and not keep long-lived mailbox access.

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `PUBLIC_BASE_URL`. The authorized redirect URI is `{PUBLIC_BASE_URL}/gmail/callback`.

Parsers, in order: schema.org `FlightReservation` / `LodgingReservation`, then `.ics` attachments, then a strict JSON prompt only when both miss and an LLM client is configured. Raw mail is not kept after a successful parse. A failure keeps the text for one retry, then deletes it.

### Sign in with Apple

The API checks the identity token against Apple's keys. The app bundle id defaults to `com.family.traveljournal` (`APPLE_CLIENT_ID`). `ALLOW_DEV_LOGIN=1` enables `POST /auth/dev` for a local HTTP server only. Leave it off otherwise.

## iPhone

Open `ios/TravelJournal/TravelJournal.xcodeproj` in Xcode 15 or later. The deployment target is iOS 17.

1. Set the development team and the Sign in with Apple capability (the entitlements file is already in the target).
2. Point the Account tab at the API. The simulator can use `http://127.0.0.1:8000`.
3. Scan Photos, confirm home, upload stays, sync Gmail, then Build on the Journal tab.

The Stays tab can load an example library so the map and home suggestion can be tried without a camera roll. Scanning the real library is the path that uses PhotoKit.

On a journal day, the phone that owns a picture shows it from Photos. Another household member sees the uploaded thumbnail.
