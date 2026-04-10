from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import Plan, BillingMode, Resource, User, UserRole
from app.core.auth import get_current_user, require_role


router = APIRouter(prefix="/plans", tags=["plans"])


# ── Schemas ─────────────────────────────────────────────────────────────────

class PlanOut(BaseModel):
    id: int
    building_id: int
    name: str
    billing_mode: str
    base_rate_uzs: float
    coin_pct: int
    coin_reset_day: int
    meeting_discount_pct: int
    meeting_discount_on: bool
    event_discount_pct: int
    event_discount_on: bool
    is_active: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class PlanCreate(BaseModel):
    building_id: int
    name: str
    billing_mode: BillingMode = BillingMode.per_unit
    base_rate_uzs: float = 0
    coin_pct: int = 25
    coin_reset_day: int = 1
    meeting_discount_pct: int = 0
    meeting_discount_on: bool = False
    event_discount_pct: int = 0
    event_discount_on: bool = False
    is_active: bool = True


class PlanPatch(BaseModel):
    name: str | None = None
    billing_mode: BillingMode | None = None
    base_rate_uzs: float | None = None
    coin_pct: int | None = None
    coin_reset_day: int | None = None
    meeting_discount_pct: int | None = None
    meeting_discount_on: bool | None = None
    event_discount_pct: int | None = None
    event_discount_on: bool | None = None
    is_active: bool | None = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("", response_model=list[PlanOut])
async def list_plans(
    building_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    query = select(Plan)
    if building_id is not None:
        query = query.where(Plan.building_id == building_id)
    result = await db.execute(query.order_by(Plan.id))
    return result.scalars().all()


@router.post("", response_model=PlanOut)
async def create_plan(
    data: PlanCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    plan = Plan(**data.model_dump())
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int,
    data: PlanPatch,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(plan, k, v)

    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    plan = await db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Check if any resources are linked to this plan
    result = await db.execute(
        select(Resource).where(Resource.plan_id == plan_id).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Cannot delete plan: resources are still linked to it",
        )

    await db.delete(plan)
    await db.commit()
