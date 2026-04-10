from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import text

from app.config import settings
from app.database import engine, Base
import app.models  # noqa: F401 — register models with Base.metadata
from app.routers.auth import router as auth_router
from app.routers.buildings import router as buildings_router
from app.routers.units_tenants import tenants_router
from app.routers.resources import router as resources_router
from app.routers.bookings import rooms_router, bookings_router
from app.routers.plans import router as plans_router
from app.routers.workspace import router as workspace_router

app = FastAPI(
    title="CBC Coworking OS — API",
    version="1.0.0",
    description="Property management platform for Modera Coworking",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (floor plan images)
upload_dir = Path(settings.UPLOAD_DIR)
upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(upload_dir)), name="static")


@app.on_event("startup")
async def startup():
    import os
    os.makedirs("/data/uploads/floor_plans", exist_ok=True)
    try:
        async with engine.begin() as conn:
            # Step 1: create any missing tables (idempotent). This also
            # creates the resources table and the resourcetype enum.
            await conn.run_sync(Base.metadata.create_all)

            # Step 2: legacy ALTERs for the old units columns. Kept so
            # the migration step below can read them.
            await conn.execute(text(
                "ALTER TABLE units ADD COLUMN IF NOT EXISTS "
                "tenant_name VARCHAR(255)"
            ))
            await conn.execute(text(
                "ALTER TABLE units ADD COLUMN IF NOT EXISTS "
                "rate_period VARCHAR(20) DEFAULT 'month'"
            ))

            # Step 3: add new resource_id columns to existing tables.
            # IF NOT EXISTS keeps this idempotent.
            await conn.execute(text(
                "ALTER TABLE zones ADD COLUMN IF NOT EXISTS "
                "resource_id INTEGER REFERENCES resources(id)"
            ))
            await conn.execute(text(
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
                "resource_id INTEGER REFERENCES resources(id)"
            ))

            # Step 4: relax old NOT NULL columns so new INSERTs (which
            # don't supply them) are valid.
            await conn.execute(text(
                "ALTER TABLE zones ALTER COLUMN zone_type DROP NOT NULL"
            ))
            await conn.execute(text(
                "ALTER TABLE zones ALTER COLUMN unit_id DROP NOT NULL"
            ))
            await conn.execute(text(
                "ALTER TABLE bookings ALTER COLUMN room_id DROP NOT NULL"
            ))

            # Step 6: new columns for improvements (lead time, discount, UZS)
            await conn.execute(text(
                "ALTER TABLE resources ADD COLUMN IF NOT EXISTS "
                "min_advance_minutes INTEGER DEFAULT 0"
            ))
            await conn.execute(text(
                "ALTER TABLE resources ADD COLUMN IF NOT EXISTS "
                "resident_discount_pct INTEGER DEFAULT 0"
            ))
            await conn.execute(text(
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
                "money_charged_uzs FLOAT DEFAULT 0"
            ))

            # Step 8: Widen userrole enum for receptionist
            try:
                await conn.execute(text(
                    "ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'receptionist'"
                ))
            except Exception:
                pass  # already exists or DB doesn't support IF NOT EXISTS

            # Step 9: Tenant extra fields
            await conn.execute(text(
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
                "unit_number VARCHAR(100)"
            ))
            await conn.execute(text(
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
                "notes TEXT"
            ))

            # Step 7: Plans feature
            await conn.execute(text(
                "ALTER TABLE resources ADD COLUMN IF NOT EXISTS "
                "plan_id INTEGER REFERENCES plans(id)"
            ))
            await conn.execute(text(
                "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
                "coin_last_reset TIMESTAMP"
            ))

            # Step 5: data migration — only if resources is empty.
            count_result = await conn.execute(
                text("SELECT count(*) FROM resources")
            )
            existing_resource_count = count_result.scalar() or 0

            if existing_resource_count == 0:
                # Check whether old units / meeting_rooms tables exist
                units_table = (await conn.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'units'"
                ))).scalar() or 0
                mr_table = (await conn.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'meeting_rooms'"
                ))).scalar() or 0

                if units_table > 0:
                    # Migrate units → resources
                    await conn.execute(text("""
                        INSERT INTO resources (
                            building_id, floor_id, name, resource_type, status,
                            area_m2, seats, monthly_rate, rate_period,
                            tenant_name, description, photos,
                            is_standalone_bookable, created_at
                        )
                        SELECT
                            f.building_id,
                            u.floor_id,
                            u.name,
                            u.unit_type::text::resourcetype,
                            u.status,
                            u.area_m2,
                            u.seats,
                            u.monthly_rate,
                            COALESCE(u.rate_period, 'month'),
                            u.tenant_name,
                            u.description,
                            u.photos,
                            TRUE,
                            u.created_at
                        FROM units u
                        JOIN floors f ON u.floor_id = f.id
                    """))

                    # Update existing zones to point at the migrated resources
                    await conn.execute(text("""
                        UPDATE zones z
                        SET resource_id = r.id
                        FROM units u, resources r
                        WHERE z.unit_id = u.id
                          AND r.name = u.name
                          AND r.floor_id = u.floor_id
                          AND z.resource_id IS NULL
                    """))

                if mr_table > 0:
                    # Copy meeting_rooms data into the resources rows we
                    # just created (matched on unit name + floor)
                    await conn.execute(text("""
                        UPDATE resources r SET
                            capacity = mr.capacity,
                            rate_coins_per_hour = mr.rate_coins_per_hour,
                            rate_money_per_hour = mr.rate_money_per_hour,
                            amenities = mr.amenities
                        FROM meeting_rooms mr
                        JOIN units u ON mr.unit_id = u.id
                        WHERE r.name = u.name
                          AND r.floor_id = u.floor_id
                          AND r.resource_type = 'meeting_room'
                    """))

                    # Update existing bookings to point at the migrated resources
                    await conn.execute(text("""
                        UPDATE bookings b
                        SET resource_id = r.id
                        FROM meeting_rooms mr, units u, resources r
                        WHERE b.room_id = mr.id
                          AND mr.unit_id = u.id
                          AND r.name = u.name
                          AND r.floor_id = u.floor_id
                          AND r.resource_type = 'meeting_room'
                          AND b.resource_id IS NULL
                    """))
    except Exception as e:
        print(f"DB init warning: {e}")


# Routers
app.include_router(auth_router)
app.include_router(buildings_router)
app.include_router(tenants_router)
app.include_router(resources_router)
app.include_router(plans_router)
app.include_router(rooms_router)
app.include_router(bookings_router)
app.include_router(workspace_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cbc-coworking-api"}
