import os
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.database import get_db
from app.models import Building, Floor, Zone, User, UserRole
from app.core.auth import get_current_user, require_role
from app.config import settings

router = APIRouter(prefix="/buildings", tags=["buildings"])


# ── Schemas ────────────────────────────────────────────────────────────────

class BuildingOut(BaseModel):
    id: int
    name: str
    address: str
    building_class: str
    total_area: float
    leasable_area: float

    class Config:
        from_attributes = True


class BuildingCreate(BaseModel):
    name: str
    address: str
    building_class: str
    total_area: float
    leasable_area: float


class FloorOut(BaseModel):
    id: int
    building_id: int
    number: int
    name: str | None
    floor_plan_url: str | None

    class Config:
        from_attributes = True


class ZoneOut(BaseModel):
    id: int
    floor_id: int
    unit_id: int | None
    points: list
    label: str | None
    zone_type: str

    class Config:
        from_attributes = True


class ZoneUpsert(BaseModel):
    unit_id: int | None = None
    points: list
    label: str | None = None
    zone_type: str


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/", response_model=list[BuildingOut])
async def list_buildings(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Building))
    return result.scalars().all()


@router.post("/", response_model=BuildingOut)
async def create_building(
    data: BuildingCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    building = Building(**data.model_dump())
    db.add(building)
    await db.commit()
    await db.refresh(building)
    return building


@router.get("/{building_id}/floors", response_model=list[FloorOut])
async def list_floors(building_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Floor).where(Floor.building_id == building_id))
    return result.scalars().all()


@router.post("/{building_id}/floors/{floor_id}/plan")
async def upload_floor_plan(
    building_id: int,
    floor_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    """Upload PNG/JPG/PDF floor plan. PDF auto-converted to PNG."""
    result = await db.execute(select(Floor).where(Floor.id == floor_id, Floor.building_id == building_id))
    floor = result.scalar_one_or_none()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    upload_dir = Path(settings.UPLOAD_DIR) / "floor_plans"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower()
    dest_path = upload_dir / f"floor_{floor_id}{ext}"

    with dest_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # PDF → PNG conversion
    if ext == ".pdf":
        from pdf2image import convert_from_path
        images = convert_from_path(str(dest_path), dpi=150, first_page=1, last_page=1)
        png_path = upload_dir / f"floor_{floor_id}.png"
        images[0].save(str(png_path), "PNG")
        dest_path.unlink()
        dest_path = png_path

    plan_url = f"/static/floor_plans/{dest_path.name}"
    floor.floor_plan_url = plan_url
    await db.commit()

    return {"floor_plan_url": plan_url}


# ── Zone (canvas polygon) endpoints ───────────────────────────────────────

@router.get("/{building_id}/floors/{floor_id}/zones", response_model=list[ZoneOut])
async def get_zones(
    building_id: int,
    floor_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    return result.scalars().all()


@router.put("/{building_id}/floors/{floor_id}/zones")
async def save_zones(
    building_id: int,
    floor_id: int,
    zones: list[ZoneUpsert],
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.manager)),
):
    """Replace all zones for a floor (full save from canvas editor)."""
    existing = await db.execute(select(Zone).where(Zone.floor_id == floor_id))
    for z in existing.scalars().all():
        await db.delete(z)

    for z in zones:
        new_zone = Zone(floor_id=floor_id, **z.model_dump())
        db.add(new_zone)

    await db.commit()
    return {"saved": len(zones)}
