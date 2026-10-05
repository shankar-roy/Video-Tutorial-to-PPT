from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.timeutil import Instant


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(Instant, default=utcnow)

    members: Mapped[list[Member]] = relationship(back_populates="household")


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    apple_sub: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    home_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_radius_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_city: Mapped[str] = mapped_column(String(200), default="")
    home_airport_iata: Mapped[str] = mapped_column(String(3), default="")
    created_at: Mapped[datetime] = mapped_column(Instant, default=utcnow)

    household: Mapped[Household] = relationship(back_populates="members")
    stays: Mapped[list[Stay]] = relationship(back_populates="member", cascade="all, delete-orphan")


class Stay(Base):
    __tablename__ = "stays"
    __table_args__ = (UniqueConstraint("member_id", "client_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(80))
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True)
    start_at: Mapped[datetime] = mapped_column(Instant)
    end_at: Mapped[datetime] = mapped_column(Instant)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    place_name: Mapped[str] = mapped_column(String(200), default="")
    photo_count: Mapped[int] = mapped_column(Integer, default=0)

    member: Mapped[Member] = relationship(back_populates="stays")
    photos: Mapped[list[StayPhoto]] = relationship(
        back_populates="stay", cascade="all, delete-orphan", order_by="StayPhoto.taken_at"
    )


class StayPhoto(Base):
    __tablename__ = "stay_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(String(200))
    taken_at: Mapped[datetime] = mapped_column(Instant)
    taken_date: Mapped[date] = mapped_column(Date)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)

    stay: Mapped[Stay] = relationship(back_populates="photos")


class Flight(Base):
    __tablename__ = "flights"
    __table_args__ = (UniqueConstraint("member_id", "gmail_message_id", "flight_number", "departure_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), default="")
    airline: Mapped[str] = mapped_column(String(120), default="")
    flight_number: Mapped[str] = mapped_column(String(16))
    origin_iata: Mapped[str] = mapped_column(String(3))
    destination_iata: Mapped[str] = mapped_column(String(3))
    departure_at: Mapped[datetime] = mapped_column(Instant)
    arrival_at: Mapped[datetime | None] = mapped_column(Instant, nullable=True)
    departure_local_date: Mapped[date] = mapped_column(Date)
    arrival_local_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmation_code: Mapped[str] = mapped_column(String(40), default="")
    origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    destination_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    destination_lon: Mapped[float | None] = mapped_column(Float, nullable=True)


class Lodging(Base):
    __tablename__ = "lodgings"
    __table_args__ = (UniqueConstraint("member_id", "gmail_message_id", "name", "check_in"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    check_in: Mapped[datetime] = mapped_column(Instant)
    check_out: Mapped[datetime | None] = mapped_column(Instant, nullable=True)
    check_in_date: Mapped[date] = mapped_column(Date)
    check_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmation_code: Mapped[str] = mapped_column(String(40), default="")


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Trip")
    title_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    start_at: Mapped[datetime] = mapped_column(Instant)
    end_at: Mapped[datetime] = mapped_column(Instant)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    primary_city: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")

    sources: Mapped[list[TripSource]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    days: Mapped[list[JournalDay]] = relationship(
        back_populates="trip", cascade="all, delete-orphan", order_by="JournalDay.day"
    )


class TripSource(Base):
    __tablename__ = "trip_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(Instant)
    end_at: Mapped[datetime] = mapped_column(Instant)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    primary_city: Mapped[str] = mapped_column(String(200), default="")
    night_count: Mapped[int] = mapped_column(Integer, default=0)

    trip: Mapped[Trip] = relationship(back_populates="sources")


class JournalDay(Base):
    __tablename__ = "journal_days"
    __table_args__ = (UniqueConstraint("trip_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    place_names: Mapped[str] = mapped_column(Text, default="[]")
    body: Mapped[str] = mapped_column(Text, default="")
    draft_body: Mapped[str] = mapped_column(Text, default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)

    trip: Mapped[Trip] = relationship(back_populates="days")
    photos: Mapped[list[JournalPhoto]] = relationship(
        back_populates="day", cascade="all, delete-orphan"
    )


class JournalPhoto(Base):
    __tablename__ = "journal_photos"
    __table_args__ = (UniqueConstraint("journal_day_id", "asset_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_day_id: Mapped[int] = mapped_column(ForeignKey("journal_days.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(200))
    taken_at: Mapped[datetime] = mapped_column(Instant)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    thumbnail_path: Mapped[str] = mapped_column(String(300), default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    candidate: Mapped[bool] = mapped_column(Boolean, default=True)

    day: Mapped[JournalDay] = relationship(back_populates="photos")


class MergeSuggestion(Base):
    __tablename__ = "merge_suggestions"
    __table_args__ = (UniqueConstraint("trip_low_id", "trip_high_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    trip_low_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    trip_high_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class ParseFailure(Base):
    __tablename__ = "parse_failures"
    __table_args__ = (UniqueConstraint("member_id", "gmail_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64))
    error: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    retries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(Instant, default=utcnow)
