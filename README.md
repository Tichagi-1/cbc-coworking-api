# CBC Coworking OS — API

FastAPI backend for Modera Coworking management platform.

## Stack
- Python 3.12 + FastAPI
- PostgreSQL via asyncpg + SQLAlchemy 2.0
- Alembic migrations
- JWT auth with role-based access
- Fabric.js zone storage (polygon JSON)
- PDF→PNG floor plan conversion via pdf2image

## Local setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in DATABASE_URL and SECRET_KEY

alembic upgrade head
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

## Railway deployment

1. Create new Railway project
2. Add PostgreSQL service → copy DATABASE_URL to env vars
3. Add Volume → mount at /data
4. Set env vars from .env.example
5. Deploy — migrations run automatically on start

## User roles

| Role    | Access |
|---------|--------|
| admin   | Full access |
| manager | Read + write (no user management) |
| tenant  | Own data + booking |
| owner   | Read-only dashboard |

## API endpoints (Sprint 1)

- `POST /auth/token` — login
- `POST /auth/register` — create user
- `GET  /buildings/` — list buildings
- `POST /buildings/` — create building
- `GET  /buildings/{id}/floors` — list floors
- `POST /buildings/{id}/floors/{fid}/plan` — upload floor plan (PNG/PDF)
- `GET  /buildings/{id}/floors/{fid}/zones` — get canvas zones
- `PUT  /buildings/{id}/floors/{fid}/zones` — save canvas zones
- `GET  /units/` — list units (filter by floor/status)
- `POST /units/` — create unit
- `PATCH /units/{id}/status` — update status
- `GET  /tenants/` — list tenants
- `POST /tenants/` — create tenant
- `POST /tenants/{id}/coins/adjust` — credit/debit coins
- `GET  /tenants/{id}/coins/history` — coin transaction log
- `GET  /health` — health check
