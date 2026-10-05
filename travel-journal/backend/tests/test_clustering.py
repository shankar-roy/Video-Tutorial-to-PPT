from datetime import datetime, timedelta, timezone

import pytest

from app.clustering import (
    PhotoPoint,
    cluster_stays,
    haversine_km,
    night_key,
    propose_home,
    select_candidate_photos,
)

UTC = timezone.utc
KM_PER_DEGREE = 6371.0088 * 3.141592653589793 / 180


def at(hour: int, day: int = 1, minute: int = 0) -> datetime:
    return datetime(2024, 6, day, hour, minute, tzinfo=UTC)


def photo(asset: str, when: datetime, lat: float, lon: float) -> PhotoPoint:
    return PhotoPoint(asset, when, lat, lon)


def test_gap_and_distance_split_stays():
    same = (37.77, -122.42)
    later = photo("b", at(8) + timedelta(hours=7), *same)
    north_lat = same[0] + (40 / KM_PER_DEGREE)
    moved = photo("c", later.taken_at + timedelta(hours=1), north_lat, same[1])
    photos = [photo("a", at(8), *same), later, moved]
    assert haversine_km(*same, north_lat, same[1]) > 30

    stays = cluster_stays(list(reversed(photos)))
    assert len(stays) == 3
    assert stays[0].client_id.startswith("2024-06-01T08:00:00|")


def test_six_hours_and_thirty_km_stay_together():
    start = photo("a", at(8), 37.77, -122.42)
    exactly_six = photo("b", at(8) + timedelta(hours=6), 37.77, -122.42)
    km_30_lat = 37.77 + (30 / KM_PER_DEGREE)
    assert haversine_km(37.77, -122.42, km_30_lat, -122.42) == pytest.approx(30, abs=0.05)
    nearby = photo("c", at(9), km_30_lat, -122.42)
    assert len(cluster_stays([start, exactly_six])) == 1
    stays = cluster_stays([start, nearby])
    assert len(stays) == 1
    assert stays[0].latitude == pytest.approx((37.77 + km_30_lat) / 2)


def test_empty_library():
    assert cluster_stays([]) == []


def test_home_is_the_place_with_the_most_nights():
    home_photos = [
        photo(f"h{day}{hour}", at(hour, day), 37.77, -122.42)
        for day, hour in ((1, 23), (2, 1), (3, 22), (5, 23), (4, 12), (4, 13))
    ]
    away = [photo(f"a{index}", at(12, 10) + timedelta(hours=index), 34.05, -118.24) for index in range(8)]
    away.append(photo("night", at(23, 10), 34.05, -118.24))
    proposal = propose_home(cluster_stays(home_photos + away))
    assert proposal is not None
    assert proposal.night_count >= 3
    assert abs(proposal.latitude - 37.77) < 0.5
    assert proposal.radius_km >= 10


def test_night_key_uses_wall_clock():
    assert night_key(at(22)) == at(22).date()
    assert night_key(at(5, 2)) == at(5, 1).date()
    assert night_key(at(6)) is None
    assert propose_home([]) is None


def test_candidate_photos_skip_bursts_and_cap_at_six():
    frames = [
        photo("burst", at(9), 38.72, -9.14),
        photo("burst-2", at(9, minute=1), 38.7201, -9.1401),
    ]
    for hour in range(10, 18):
        frames.append(photo(f"h{hour}", at(hour), 38.72, -9.14))
    chosen = select_candidate_photos(frames)
    assert len(chosen) == 6
    assert chosen[0].asset_id == "burst"
    assert all(item.asset_id != "burst-2" for item in chosen)
