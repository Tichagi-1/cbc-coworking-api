from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import Tenant, CoinTransaction, User, UserRole, CoinTxReason
from app.core.auth import get_current_user, require_role


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
