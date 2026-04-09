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


# Legacy — kept so the existing PG enum type and old Zone.zone_type column
# (now nullable) still resolve. New code should use ResourceType.
class UnitType(str, enum.Enum):
    office = "office"
    meeting_room = "meeting_room"
    hot_desk = "hot_desk"
    open_space = "open_space"


class ResourceType(str, enum.Enum):
    office = "office"
    meeting_room = "meeting_room"
    hot_desk = "hot_desk"
    open_space = "open_space"
    amenity = "amenity"


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
    zones: Mapped[list["Zone"]] = relationship(back_populates="floor")


class Resource(Base):
    """
    Unified catalog row for any bookable / leasable space:
    offices, meeting rooms, hot desks, open spaces, amenities.
    """
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    floor_id: Mapped[int | None] = mapped_column(ForeignKey("floors.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(255))
    resource_type: Mapped[ResourceType] = mapped_column(Enum(ResourceType))
    status: Mapped[UnitStatus] = mapped_column(Enum(UnitStatus), default=UnitStatus.vacant)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tenant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # office / hot_desk / open_space fields
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_period: Mapped[str | None] = mapped_column(String(20), nullable=True, default="month")

    # meeting_room fields
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_coins_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_money_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    amenities: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # amenity fields
    rate_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_standalone_bookable: Mapped[bool] = mapped_column(Boolean, default=True)

    zoho_contract_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    building: Mapped[Building] = relationship()
    floor: Mapped["Floor | None"] = relationship()
    zones: Mapped[list["Zone"]] = relationship(back_populates="resource")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="resource")


class Zone(Base):
    """Canvas polygon overlaid on floor plan image."""
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id"))
    # NEW: link to a Resource. Old unit_id and zone_type columns are kept
    # in the DB as nullable legacy and not exposed by the model anymore.
    resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("resources.id"), nullable=True
    )
    points: Mapped[dict] = mapped_column(JSON)  # [{x, y}, ...]
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    floor: Mapped[Floor] = relationship(back_populates="zones")
    resource: Mapped["Resource | None"] = relationship(back_populates="zones")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    company_name: Mapped[str] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plan_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    monthly_rate: Mapped[float] = mapped_column(Float, default=0)
    coin_balance: Mapped[float] = mapped_column(Float, default=0)
    is_resident: Mapped[bool] = mapped_column(Boolean, default=True)
    zoho_contact_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="tenant")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="tenant")
    coin_transactions: Mapped[list["CoinTransaction"]] = relationship(back_populates="tenant")


class Lease(Base):
    """
    Kept so the snapshot endpoint and any future lease history feature
    can still query the existing leases table. The Tenant.leases
    relationship has been intentionally dropped — query leases directly
    via select(Lease).where(...) instead.
    """
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


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NEW: resource_id is the canonical link. The old room_id column is
    # kept in the DB as nullable legacy.
    resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("resources.id"), nullable=True
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    payment_type: Mapped[BookingPaymentType] = mapped_column(Enum(BookingPaymentType))
    coins_charged: Mapped[float] = mapped_column(Float, default=0)
    money_charged: Mapped[float] = mapped_column(Float, default=0)
    zoho_invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    resource: Mapped["Resource | None"] = relationship(back_populates="bookings")
    tenant: Mapped[Tenant] = relationship(back_populates="bookings")


class CoinTransaction(Base):
    __tablename__ = "coin_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    delta: Mapped[float] = mapped_column(Float)
    reason: Mapped[CoinTxReason] = mapped_column(Enum(CoinTxReason))
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="coin_transactions")
