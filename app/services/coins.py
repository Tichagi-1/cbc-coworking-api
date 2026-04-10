from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Tenant, Resource, Plan, CoinTransaction, CoinTxReason


async def calculate_tenant_coins(tenant_id: int, db: AsyncSession) -> tuple[float, list[dict]]:
    """Calculate total coins to accrue based on tenant's occupied resources and their plans."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return 0.0, []

    # Find resources where tenant_name matches this tenant's company_name
    # (temporary approach until proper lease system exists)
    result = await db.execute(
        select(Resource).where(
            Resource.tenant_name == tenant.company_name,
            Resource.status == "occupied",
        )
    )
    resources = result.scalars().all()

    breakdown = []
    total = 0.0
    for r in resources:
        # Calculate effective monthly rate
        if r.plan_id:
            plan = await db.get(Plan, r.plan_id)
            if plan:
                if plan.billing_mode.value == "per_seat":
                    monthly = plan.base_rate_uzs * (r.seats or 1)
                else:
                    monthly = plan.base_rate_uzs
                coin_pct = plan.coin_pct
            else:
                monthly = r.monthly_rate or 0
                coin_pct = 25
        else:
            monthly = r.monthly_rate or 0
            coin_pct = 25

        coins = monthly * coin_pct / 100
        total += coins
        breakdown.append({
            "resource_name": r.name,
            "monthly_rate_uzs": monthly,
            "coin_pct": coin_pct,
            "coins": coins,
        })

    return total, breakdown


async def reset_tenant_coins(tenant_id: int, db: AsyncSession) -> dict:
    """Reset coin_balance to calculated amount. Log transaction."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError("Tenant not found")

    new_balance, breakdown = await calculate_tenant_coins(tenant_id, db)
    old_balance = tenant.coin_balance
    tenant.coin_balance = round(new_balance, 2)
    tenant.coin_last_reset = datetime.now()

    # Log the reset as a coin transaction
    tx = CoinTransaction(
        tenant_id=tenant_id,
        delta=round(new_balance - old_balance, 2),
        reason=CoinTxReason.monthly_accrual,
        note=f"Monthly coin reset: {len(breakdown)} resources",
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tenant)

    return {
        "coin_balance": tenant.coin_balance,
        "coin_last_reset": tenant.coin_last_reset.isoformat() if tenant.coin_last_reset else None,
        "breakdown": breakdown,
    }
