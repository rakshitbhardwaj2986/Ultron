from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is missing. Add it to .env, for example: "
        "DATABASE_URL=postgresql+psycopg2://postgres:your_password@localhost:5432/ultron"
    )

# pool_pre_ping checks a connection is still alive before using it —
# without this, Neon/Supabase free-tier Postgres silently closes idle
# connections and the next request fails with a confusing SSL error.
# pool_recycle forces connections to refresh before that idle timeout hits.

engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_pre_ping=True,
    pool_recycle=280,
)
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()