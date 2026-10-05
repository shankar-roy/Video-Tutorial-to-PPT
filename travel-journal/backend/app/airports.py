"""OurAirports coordinates, filtered to airports that have an IATA code."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.clustering import haversine_km

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "airports.json"
HOME_AIRPORT_MAX_KM = 200.0


class Airports:
    def __init__(self, rows: list[dict]):
        self.by_iata = {row["iata"]: row for row in rows}

    def get(self, iata: str | None) -> dict | None:
        if not iata:
            return None
        return self.by_iata.get(iata.strip().upper())

    def codes(self) -> set[str]:
        return set(self.by_iata)

    def nearest(self, latitude: float, longitude: float, max_km: float = HOME_AIRPORT_MAX_KM) -> dict | None:
        best = None
        best_distance = max_km
        for row in self.by_iata.values():
            distance = haversine_km(latitude, longitude, row["lat"], row["lon"])
            if distance <= best_distance:
                best = row
                best_distance = distance
        return best


@lru_cache(maxsize=1)
def load_airports(path: str = str(DATA_PATH)) -> Airports:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return Airports(rows)
