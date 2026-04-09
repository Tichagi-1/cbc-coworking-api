from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models import Unit, Tenant, Lease, CoinTransaction, UnitStatus, UnitType, UserRole, CoinTxReason
from app.core.auth import get_current_user, require_role

# ── Units ──────────────────────────────────────────────────────────────────
units_router = APIRouter(prefix="/units", tags=["units"])


class UnitOut(BaseModel):
    id: int
    floor_id: int
    name: str
    unit_type: str
    status: str
    area_m2: float
    seats: int
    monthly_rate: float
    rate_period: str | None = "month"
    tenant_name: str | None = None
    description: str | None
    photos: list | None

    class Config:
        from_attributes = True


class UnitCreate(BaseModel):
    floor_id: int
    name: str
    unit_type: UnitType
    area_m2: float
    seats: int = 1
    monthly_rate: float = 0
    rate_period: str | None = "month"
    description: str | None = None


class UnitPatch(BaseModel):
    name: str | None = None
    area_m2: float | None = None
    seats: int | None = None
    monthly_rate: float | None = None
    rate_period: str | None = None
    status: UnitStatus | None = None
    tenant_name: str | None = None
    description: str | None = None


@units_router.get("/", response_model=list[UnitOut])
async def list_units(
    floor_id: int | None = None,
    status: UnitStatus | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    query = select(Unit)
    if floor_id:
        query = query.where(Unit.floor_id == floor_id)
    if status:
        query = query.where(Unit.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@units_router.get("/{unit_id}", response_model=UnitOut)
async def get_unit(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    unit = await db.get(Unit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    return unit


@units_router.post("/", response_model=UnitOut)
async def create_unit(
    data: UnitCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    unit = Unit(**data.model_dump())
    db.add(unit)
    await db.commit()
    await db.refresh(unit)
    return unit


@units_router.patch("/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: int,
    data: UnitPatch,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    unit = await db.get(Unit, unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(unit, k, v)

    await db.commit()
    await db.refresh(unit)
    return unit


@units_router.patch("/{unit_id}/status")
async def update_unit_status(
    unit_id: int,
    status: UnitStatus,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    result = await db.execute(select(Unit).where(Unit.id == unit_id))
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    unit.status = status
    await db.commit()
    return {"id": unit_id, "status": status}


# ── Tenants ────────────────────────────────────────────────────────────────
tenants_router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantOut(BaseModel):
    id: int
    user_id: int
    company_name: str
    contact_name: str | None
    plan_type: str | None
    monthly_rate: float
    coin_balance: float
    is_resident: bool

    class Config:
        from_attributes = True


class TenantCreate(BaseModel):
    user_id: int
    company_name: str
    contact_name: str | None = None
    contact_phone: str | None = None
    plan_type: str | None = None
    monthly_rate: float = 0
    is_resident: bool = True


class CoinAdjust(BaseModel):
    delta: float
    note: str | None = None


@tenants_router.get("/", response_model=list[TenantOut])
async def list_tenants(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Tenant))
    return result.scalars().all()


@tenants_router.post("/", response_model=TenantOut)
async def create_tenant(
    data: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    tenant = Tenant(**data.model_dump())
    # Auto-accrue coins: 25% of monthly rate
    if tenant.monthly_rate > 0:
        tenant.coin_balance = round(tenant.monthly_rate * 0.25, 2)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@tenants_router.post("/{tenant_id}/coins/adjust")
async def adjust_coins(
    tenant_id: int,
    data: CoinAdjust,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    """Manually credit or debit coins (admin only)."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.coin_balance = round(tenant.coin_balance + data.delta, 2)
    tx = CoinTransaction(
        tenant_id=tenant_id,
        delta=data.delta,
        reason=CoinTxReason.manual_admin,
        note=data.note,
    )
    db.add(tx)
    await db.commit()
    return {"tenant_id": tenant_id, "new_balance": tenant.coin_balance}


@tenants_router.get("/{tenant_id}/coins/history")
async def coin_history(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(CoinTransaction)
        .where(CoinTransaction.tenant_id == tenant_id)
        .order_by(CoinTransaction.created_at.desc())
        .limit(100)
    )
    txs = result.scalars().all()
    return [{"id": t.id, "delta": t.delta, "reason": t.reason, "note": t.note, "created_at": t.created_at} for t in txs]
