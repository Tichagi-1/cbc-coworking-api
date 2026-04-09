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
from app.routers.units_tenants import units_router, tenants_router

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
            # Create any missing tables (idempotent — only adds tables, not columns)
            await conn.run_sync(Base.metadata.create_all)
            # In-place column additions for tables that already exist.
            # IF NOT EXISTS keeps this idempotent across restarts.
            await conn.execute(
                text(
                    "ALTER TABLE units ADD COLUMN IF NOT EXISTS tenant_name VARCHAR(255)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE units ADD COLUMN IF NOT EXISTS rate_period VARCHAR(20) DEFAULT 'month'"
                )
            )
    except Exception as e:
        print(f"DB init warning: {e}")


# Routers
app.include_router(auth_router)
app.include_router(buildings_router)
app.include_router(units_router)
app.include_router(tenants_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cbc-coworking-api"}
