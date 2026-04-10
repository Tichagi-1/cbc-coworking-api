from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Tenant
from app.services.coins import reset_tenant_coins


async def run_monthly_coin_reset(db: AsyncSession, force: bool = False) -> int:
    """Reset coins for all tenants. By default only runs on the 1st."""
    today = date.today()
    if not force and today.day != 1:
        return 0

    result = await db.execute(select(Tenant))
    tenants = result.scalars().all()

    reset_count = 0
    for tenant in tenants:
        if tenant.coin_last_reset:
            last = tenant.coin_last_reset
            if last.year == today.year and last.month == today.month:
                continue
        try:
            await reset_tenant_coins(tenant.id, db)
            reset_count += 1
        except Exception as e:
            print(f"Coin reset failed for tenant {tenant.id}: {e}")

    print(f"Monthly coin reset: {reset_count} tenants on {today}")
    return reset_count
