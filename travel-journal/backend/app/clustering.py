"""Stay and home detection.

The iPhone app clusters photos before any account exists. The same rules
live in ios/.../StayClusterer.swift. Keep the two copies in step:

- A new stay starts when the gap since the previous located photo is over
  6 hours, or the move is over 30 km.
- Home is the cluster of stays within 50 km that contains the most nights.
  A night photo is one taken at 22:00 or later (that date) or before 06:00
  (the previous date). The person confirms the city and radius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

EARTH_RADIUS_KM = 6371.0088
GAP = timedelta(hours=6)
SPLIT_KM = 30.0
HOME_CLUSTER_KM = 50.0
BURST_SECONDS = 120
BURST_KM = 0.1
MAX_CANDIDATE_PHOTOS = 6


@dataclass(frozen=True)
class PhotoPoint:
    asset_id: str
    taken_at: datetime
    latitude: float
    longitude: float


@dataclass
class StayCluster:
    client_id: str
    start_at: datetime
    end_at: datetime
    latitude: float
    longitude: float
    photos: list[PhotoPoint] = field(default_factory=list)

    @property
    def photo_count(self) -> int:
        return len(self.photos)


@dataclass(frozen=True)
class HomeProposal:
    latitude: float
    longitude: float
    radius_km: float
    night_count: int
    photo_count: int


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _sort_key(photo: PhotoPoint) -> tuple[float, str]:
    taken = photo.taken_at
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=_UTC)
    return (taken.timestamp(), photo.asset_id)


def stay_client_id(start_at: datetime, latitude: float, longitude: float) -> str:
    return f"{start_at.strftime('%Y-%m-%dT%H:%M:%S')}|{latitude:.3f}|{longitude:.3f}"


def night_key(taken_at: datetime) -> date | None:
    """Night identity using the datetime's own wall clock."""
    hour = taken_at.hour
    if hour >= 22:
        return taken_at.date()
    if hour < 6:
        return taken_at.date() - timedelta(days=1)
    return None


def cluster_stays(photos: list[PhotoPoint]) -> list[StayCluster]:
    located = sorted(photos, key=_sort_key)
    if not located:
        return []
    groups: list[list[PhotoPoint]] = [[located[0]]]
    for photo in located[1:]:
        previous = groups[-1][-1]
        gap = _elapsed(photo.taken_at, previous.taken_at)
        distance = haversine_km(
            previous.latitude, previous.longitude, photo.latitude, photo.longitude
        )
        if gap > GAP or distance > SPLIT_KM:
            groups.append([photo])
        else:
            groups[-1].append(photo)
    stays: list[StayCluster] = []
    for group in groups:
        latitude = sum(photo.latitude for photo in group) / len(group)
        longitude = sum(photo.longitude for photo in group) / len(group)
        stays.append(
            StayCluster(
                client_id=stay_client_id(group[0].taken_at, latitude, longitude),
                start_at=group[0].taken_at,
                end_at=group[-1].taken_at,
                latitude=latitude,
                longitude=longitude,
                photos=list(group),
            )
        )
    return stays


def _elapsed(later: datetime, earlier: datetime) -> timedelta:
    if later.tzinfo is None and earlier.tzinfo is not None:
        later = later.replace(tzinfo=earlier.tzinfo)
    if earlier.tzinfo is None and later.tzinfo is not None:
        earlier = earlier.replace(tzinfo=later.tzinfo)
    return later - earlier


def propose_home(stays: list[StayCluster]) -> HomeProposal | None:
    if not stays:
        return None
    ordered = sorted(stays, key=lambda stay: (-stay.photo_count, stay.start_at, stay.client_id))
    clusters: list[list[StayCluster]] = []
    for stay in ordered:
        placed = False
        for cluster in clusters:
            centroid = _centroid(cluster)
            if haversine_km(stay.latitude, stay.longitude, centroid[0], centroid[1]) <= HOME_CLUSTER_KM:
                cluster.append(stay)
                placed = True
                break
        if not placed:
            clusters.append([stay])

    def score(cluster: list[StayCluster]) -> tuple[int, int, datetime]:
        nights: set[date] = set()
        for stay in cluster:
            for photo in stay.photos:
                key = night_key(photo.taken_at)
                if key is not None:
                    nights.add(key)
        earliest = min(stay.start_at for stay in cluster)
        return (len(nights), sum(stay.photo_count for stay in cluster), earliest)

    # Most nights, then most photos, then the earlier cluster.
    best = max(
        clusters,
        key=lambda cluster: (score(cluster)[0], score(cluster)[1], -_sort_dt(score(cluster)[2])),
    )
    nights, photos, _ = score(best)
    latitude, longitude = _centroid(best)
    farthest = max(haversine_km(latitude, longitude, stay.latitude, stay.longitude) for stay in best)
    radius = min(80.0, max(10.0, farthest + 5.0))
    return HomeProposal(
        latitude=latitude,
        longitude=longitude,
        radius_km=radius,
        night_count=nights,
        photo_count=photos,
    )


def select_candidate_photos(
    photos: list[PhotoPoint],
    *,
    limit: int = MAX_CANDIDATE_PHOTOS,
    burst_seconds: int = BURST_SECONDS,
    burst_km: float = BURST_KM,
) -> list[PhotoPoint]:
    """Spread a handful of frames across a day and collapse shutter bursts."""
    ordered = sorted(photos, key=_sort_key)
    kept: list[PhotoPoint] = []
    for photo in ordered:
        if kept:
            gap = _elapsed(photo.taken_at, kept[-1].taken_at).total_seconds()
            distance = haversine_km(
                kept[-1].latitude, kept[-1].longitude, photo.latitude, photo.longitude
            )
            if gap < burst_seconds and distance < burst_km:
                continue
        kept.append(photo)
    if len(kept) <= limit:
        return kept
    return [kept[index] for index in _spread_indices(len(kept), limit)]


def _spread_indices(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    chosen: list[int] = []
    for step in range(limit):
        index = round(step * (count - 1) / (limit - 1))
        if index not in chosen:
            chosen.append(index)
    cursor = 0
    while len(chosen) < limit and cursor < count:
        if cursor not in chosen:
            chosen.append(cursor)
        cursor += 1
    return sorted(chosen)


def _centroid(stays: list[StayCluster]) -> tuple[float, float]:
    latitude = sum(stay.latitude for stay in stays) / len(stays)
    longitude = sum(stay.longitude for stay in stays) / len(stays)
    return latitude, longitude


def _sort_dt(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=_UTC)
    return value.timestamp()


_UTC = timezone.utc
