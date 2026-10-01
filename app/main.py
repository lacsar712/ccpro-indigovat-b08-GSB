import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.db import Base, SessionLocal, engine
from app.routers import auth, pages
from app.seed import ensure_seed_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # create_all 不会为已存在的旧库补建索引，这里幂等补建浸染去重用的部分唯一索引。
    # 旧库若已被连点刷出重复行，先按 (缸,时间,米数,电位) 去重、只留最早一笔，再建唯一索引。
    with engine.begin() as conn:
        conn.execute(
            text(
                'DELETE FROM dip_lots a USING dip_lots b '
                'WHERE a."redoxMv" IS NULL AND b."redoxMv" IS NULL '
                'AND a."vat_id" = b."vat_id" '
                'AND a."dippedAt" = b."dippedAt" '
                'AND a."clothMeters" = b."clothMeters" '
                'AND a.id > b.id'
            )
        )
        conn.execute(
            text(
                'DELETE FROM dip_lots a USING dip_lots b '
                'WHERE a."redoxMv" IS NOT NULL AND b."redoxMv" IS NOT NULL '
                'AND a."vat_id" = b."vat_id" '
                'AND a."dippedAt" = b."dippedAt" '
                'AND a."clothMeters" = b."clothMeters" '
                'AND a."redoxMv" = b."redoxMv" '
                'AND a.id > b.id'
            )
        )
        conn.execute(
            text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "uniq_dip_lot_redox_null" '
                'ON dip_lots ("vat_id", "dippedAt", "clothMeters") '
                'WHERE "redoxMv" IS NULL'
            )
        )
        conn.execute(
            text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "uniq_dip_lot_redox_set" '
                'ON dip_lots ("vat_id", "dippedAt", "clothMeters", "redoxMv") '
                'WHERE "redoxMv" IS NOT NULL'
            )
        )
    db = SessionLocal()
    try:
        ensure_seed_data(db)
    finally:
        db.close()
    yield


app = FastAPI(title="IndigoVat 染缸还原台", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-indigovat-session-secret"),
    session_cookie="indigovat_session",
    same_site="lax",
    https_only=False,
)

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth.router)
app.include_router(pages.router)
