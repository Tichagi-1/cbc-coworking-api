from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import Tenant, CoinTransaction, User, UserRole, CoinTxReason
from app.core.auth import get_current_user, require_role
from app.services.coins import calculate_tenant_coins, reset_tenant_coins


# units_router was removed in the resource catalog refactor — Unit/MeetingRoom
# are now unified into Resource. See app/routers/resources.py.


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


class TenantPatch(BaseModel):
    company_name: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    plan_type: str | None = None
    monthly_rate: float | None = None
    is_resident: bool | None = None


class CoinAdjust(BaseModel):
    delta: float
    note: str | None = None


@tenants_router.get("/", response_model=list[TenantOut])
async def list_tenants(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Tenant))
    return result.scalars().all()


@tenants_router.get("/me", response_model=TenantOut | None)
async def get_my_tenant(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the tenant record linked to the current user, or null."""
    result = await db.execute(select(Tenant).where(Tenant.user_id == user.id))
    return result.scalar_one_or_none()


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


@tenants_router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@tenants_router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: int,
    data: TenantPatch,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(tenant, k, v)

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


@tenants_router.post("/{tenant_id}/coins/reset")
async def reset_coins(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    """Reset coin balance based on tenant's occupied resources and their plans."""
    try:
        result = await reset_tenant_coins(tenant_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@tenants_router.get("/{tenant_id}/coin-summary")
async def coin_summary(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Return breakdown of coins by resource, current balance, and next reset info."""
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    total_coins, breakdown = await calculate_tenant_coins(tenant_id, db)

    # Compute next_reset: coin_last_reset + 1 month (approximate via coin_reset_day)
    next_reset = None
    if tenant.coin_last_reset:
        from datetime import datetime
        month = tenant.coin_last_reset.month % 12 + 1
        year = tenant.coin_last_reset.year + (1 if month == 1 else 0)
        try:
            next_reset = tenant.coin_last_reset.replace(year=year, month=month, day=1).isoformat()
        except ValueError:
            next_reset = None

    return {
        "tenant_id": tenant.id,
        "company_name": tenant.company_name,
        "coin_balance": tenant.coin_balance,
        "coin_last_reset": tenant.coin_last_reset.isoformat() if tenant.coin_last_reset else None,
        "next_reset": next_reset,
        "projected_coins": round(total_coins, 2),
        "breakdown": breakdown,
    }
