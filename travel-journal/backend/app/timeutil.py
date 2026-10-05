"""Datetime helpers. Instants are stored as UTC ISO strings; journal days use wall dates."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.types import String, TypeDecorator


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def dump_dt(value: datetime) -> str:
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def load_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def wall_date(value: datetime) -> date:
    """Calendar date of the original clock time, before any UTC conversion."""
    return value.date()


class Instant(TypeDecorator):
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return dump_dt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return load_dt(value)
