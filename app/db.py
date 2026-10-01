import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "6120")
    name = os.environ.get("POSTGRES_DB", "indigovat")
    user = os.environ.get("POSTGRES_USER", "indigovat")
    password = os.environ.get("POSTGRES_PASSWORD", "indigovat")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


DATABASE_URL = os.environ.get("DATABASE_URL") or _database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def run_lightweight_migrations(bind_engine) -> None:
    """对既有库做幂等小迁移：dip_lots 增加提交凭证列并加唯一约束。

    顺序：加可空列 → 逐行回填随机唯一值 → 建唯一索引 → 置 NOT NULL。
    每步都 IF NOT EXISTS / 可重入，多副本同时启动也不致损坏。
    """
    inspector = inspect(bind_engine)
    if "dip_lots" not in inspector.get_table_names():
        return  # 全新库，create_all 会按最新模型直接建出
    columns = {c["name"] for c in inspector.get_columns("dip_lots")}
    with bind_engine.begin() as conn:
        if "submit_token" not in columns:
            conn.execute(
                text("ALTER TABLE dip_lots ADD COLUMN submit_token VARCHAR(64)")
            )
        # 逐行回填：随机值 + 行 id + 时钟，保证互不相同
        conn.execute(
            text(
                "UPDATE dip_lots SET submit_token = "
                "md5(random()::text || id::text || clock_timestamp()::text) "
                "WHERE submit_token IS NULL"
            )
        )
        # 唯一索引（幂等）：约束/索引同名，存在则跳过
        exists = conn.execute(
            text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'uniq_dip_lot_submit_token'"
            )
        ).first()
        if exists is None:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uniq_dip_lot_submit_token ON dip_lots (submit_token)"
                )
            )
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'dip_lots' AND column_name = 'submit_token'"
            )
        ).scalar()
        if nullable == "YES":
            conn.execute(
                text("ALTER TABLE dip_lots ALTER COLUMN submit_token SET NOT NULL")
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
