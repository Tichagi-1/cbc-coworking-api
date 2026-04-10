from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import UserRole
from app.core.auth import require_role
from app.services.cron import run_monthly_coin_reset

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/coins/reset-all")
async def trigger_coin_reset(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    """Force reset coins for all tenants regardless of date."""
    count = await run_monthly_coin_reset(db, force=True)
    return {
        "reset_count": count,
        "triggered_at": datetime.now().isoformat(),
        "note": "Force reset regardless of date",
    }
