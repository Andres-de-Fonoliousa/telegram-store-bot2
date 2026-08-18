# core/database.py
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Migrations for existing databases (idempotent — missing column errors are ignored)
def run_migrations():
    if "sqlite" not in settings.DATABASE_URL:
        return
    statements = [
        "ALTER TABLE orders ADD COLUMN refunded BOOLEAN DEFAULT 0",
        "ALTER TABLE deposit_orders ADD COLUMN balance_credited BOOLEAN DEFAULT 0",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass

# Dependency to get DB session (used in bot handlers later)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()