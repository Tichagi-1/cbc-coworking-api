import enum
from datetime import datetime
from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, Enum,
    ForeignKey, Text, JSON, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


# ── Enums ──────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    tenant = "tenant"
    owner = "owner"


class UnitType(str, enum.Enum):
    office = "office"
    meeting_room = "meeting_room"
    hot_desk = "hot_desk"
    open_space = "open_space"


class UnitStatus(str, enum.Enum):
    vacant = "vacant"
    occupied = "occupied"
    reserved = "reserved"


class LeaseStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    terminated = "terminated"
    pending = "pending"


class BookingPaymentType(str, enum.Enum):
    coins = "coins"
    money = "money"


class CoinTxReason(str, enum.Enum):
    monthly_accrual = "monthly_accrual"
    manual_admin = "manual_admin"
    booking_debit = "booking_debit"
    refund = "refund"


# ── Models ─────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.tenant)
    telegram_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tenant: Mapped["Tenant | None"] = relationship(back_populates="user")


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(500))
    building_class: Mapped[str] = mapped_column(String(10))  # A, B, C
    total_area: Mapped[float] = mapped_column(Float)
    leasable_area: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    floors: Mapped[list["Floor"]] = relationship(back_populates="building")


class Floor(Base):
    __tablename__ = "floors"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    floor_plan_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    building: Mapped[Building] = relationship(back_populates="floors")
    units: Mapped[list["Unit"]] = relationship(back_populates="floor")
    zones: Mapped[list["Zone"]] = relationship(back_populates="floor")


class Zone(Base):
    """Canvas polygon overlaid on floor plan image."""
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    points: Mapped[dict] = mapped_column(JSON)  # [{x, y}, ...]
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zone_type: Mapped[UnitType] = mapped_column(Enum(UnitType))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    floor: Mapped[Floor] = relationship(back_populates="zones")
    unit: Mapped["Unit | None"] = relationship(back_populates="zone")


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id"))
    name: Mapped[str] = mapped_column(String(100))          # e.g. "Office 201"
    unit_type: Mapped[UnitType] = mapped_column(Enum(UnitType))
    status: Mapped[UnitStatus] = mapped_column(Enum(UnitStatus), default=UnitStatus.vacant)
    area_m2: Mapped[float] = mapped_column(Float)
    seats: Mapped[int] = mapped_column(Integer, default=1)
    monthly_rate: Mapped[float] = mapped_column(Float, default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photos: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [url, ...]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    floor: Mapped[Floor] = relationship(back_populates="units")
    zone: Mapped["Zone | None"] = relationship(back_populates="unit")
    leases: Mapped[list["Lease"]] = relationship(back_populates="unit")
    meeting_room: Mapped["MeetingRoom | None"] = relationship(back_populates="unit")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    company_name: Mapped[str] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plan_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "Enterprise"
    monthly_rate: Mapped[float] = mapped_column(Float, default=0)
    coin_balance: Mapped[float] = mapped_column(Float, default=0)
    # Coins = 25% of monthly_rate, or set manually by admin
    is_resident: Mapped[bool] = mapped_column(Boolean, default=True)
    zoho_contact_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="tenant")
    leases: Mapped[list["Lease"]] = relationship(back_populates="tenant")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="tenant")
    coin_transactions: Mapped[list["CoinTransaction"]] = relationship(back_populates="tenant")


class Lease(Base):
    __tablename__ = "leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime] = mapped_column(DateTime)
    monthly_rate: Mapped[float] = mapped_column(Float)
    status: Mapped[LeaseStatus] = mapped_column(Enum(LeaseStatus), default=LeaseStatus.pending)
    zoho_contract_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    unit: Mapped[Unit] = relationship(back_populates="leases")
    tenant: Mapped[Tenant] = relationship(back_populates="leases")


class MeetingRoom(Base):
    __tablename__ = "meeting_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int] = mapped_column(Integer)
    rate_coins_per_hour: Mapped[float] = mapped_column(Float)   # for residents
    rate_money_per_hour: Mapped[float] = mapped_column(Float)   # for non-residents / overage
    amenities: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["TV", "Whiteboard"]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    unit: Mapped[Unit] = relationship(back_populates="meeting_room")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="room")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("meeting_rooms.id"))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    payment_type: Mapped[BookingPaymentType] = mapped_column(Enum(BookingPaymentType))
    coins_charged: Mapped[float] = mapped_column(Float, default=0)
    money_charged: Mapped[float] = mapped_column(Float, default=0)
    zoho_invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="web")  # web | telegram
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    room: Mapped[MeetingRoom] = relationship(back_populates="bookings")
    tenant: Mapped[Tenant] = relationship(back_populates="bookings")


class CoinTransaction(Base):
    __tablename__ = "coin_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    delta: Mapped[float] = mapped_column(Float)           # positive = credit, negative = debit
    reason: Mapped[CoinTxReason] = mapped_column(Enum(CoinTxReason))
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # booking_id etc.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="coin_transactions")
