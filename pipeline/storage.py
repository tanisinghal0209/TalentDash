import os
import uuid
import asyncpg
import aiosqlite
from loguru import logger
from dotenv import load_dotenv

# Ensure dotenv is loaded
load_dotenv()

try:
    from validator import SalaryRecord
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from validator import SalaryRecord

def is_sqlite_conn(conn) -> bool:
    """Helper to check if the connection object is SQLite."""
    type_str = str(type(conn)).lower()
    return "sqlite" in type_str or hasattr(conn, "execute") and not hasattr(conn, "fetch")

async def store_records(records: list[SalaryRecord], conn=None) -> int:
    """Inserts valid SalaryRecord objects one-by-one. Logs each success/failure.
    If no connection is supplied, creates and manages a new connection pool/session.
    """
    if not records:
        return 0

    db_url = os.getenv("DATABASE_URL", "sqlite:///data/salaries.db")
    is_sqlite = db_url.startswith("sqlite://")
    
    close_conn = False
    if conn is None:
        close_conn = True
        if is_sqlite:
            db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
            if os.path.dirname(db_path):
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
            logger.info(f"Connecting to SQLite database: {db_path}")
            conn = await aiosqlite.connect(db_path)
            # Ensure SQLite table exists
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS salary_records (
                id TEXT PRIMARY KEY,
                company VARCHAR NOT NULL,
                company_slug VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                level_standardized VARCHAR NOT NULL,
                location VARCHAR NOT NULL,
                currency VARCHAR NOT NULL,
                experience_years INTEGER NOT NULL,
                base_salary NUMERIC NOT NULL,
                bonus NUMERIC NOT NULL DEFAULT 0,
                stock NUMERIC NOT NULL DEFAULT 0,
                total_compensation NUMERIC NOT NULL,
                source VARCHAR NOT NULL,
                confidence_score FLOAT NOT NULL,
                submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_verified BOOLEAN NOT NULL DEFAULT 0
            );
            """)
            await conn.commit()
        else:
            logger.info("Connecting to PostgreSQL database...")
            conn = await asyncpg.connect(db_url)
            # Ensure PostgreSQL table exists
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS salary_records (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                company VARCHAR NOT NULL,
                company_slug VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                level_standardized VARCHAR NOT NULL,
                location VARCHAR NOT NULL,
                currency VARCHAR NOT NULL,
                experience_years INTEGER NOT NULL,
                base_salary NUMERIC NOT NULL,
                bonus NUMERIC NOT NULL DEFAULT 0,
                stock NUMERIC NOT NULL DEFAULT 0,
                total_compensation NUMERIC GENERATED ALWAYS AS (base_salary + bonus + stock) STORED,
                source VARCHAR NOT NULL,
                confidence_score FLOAT NOT NULL,
                submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                is_verified BOOLEAN NOT NULL DEFAULT FALSE
            );
            """)

    stored_count = 0
    for record in records:
        try:
            if is_sqlite_conn(conn):
                insert_query = """
                INSERT INTO salary_records (
                    id, company, company_slug, role, level_standardized, location, 
                    currency, experience_years, base_salary, bonus, stock, 
                    total_compensation, source, confidence_score, is_verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                uid = str(uuid.uuid4())
                tc = record.base_salary + record.bonus + record.stock
                await conn.execute(insert_query, (
                    uid, record.company, record.company_slug, record.role, 
                    record.level_standardized.value, record.location, record.currency, 
                    record.experience_years, record.base_salary, record.bonus, record.stock, 
                    tc, record.source, record.confidence_score, 1 if record.is_verified else 0
                ))
                await conn.commit()
            else:
                insert_query = """
                INSERT INTO salary_records (
                    company, company_slug, role, level_standardized, location, 
                    currency, experience_years, base_salary, bonus, stock, 
                    source, confidence_score, is_verified
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13);
                """
                await conn.execute(insert_query, 
                    record.company, record.company_slug, record.role, 
                    record.level_standardized.value, record.location, record.currency, 
                    record.experience_years, record.base_salary, record.bonus, record.stock, 
                    record.source, record.confidence_score, record.is_verified
                )
            stored_count += 1
            logger.info(f"Successfully stored record for {record.company} ({record.role})")
        except Exception as e:
            logger.error(f"Failed to store record for {record.company} ({record.role}): {e}")

    if close_conn:
        await conn.close()

    return stored_count

class Storage:
    """Class wrapper for backward compatibility in the pipeline."""
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.is_sqlite = database_url.startswith("sqlite://")
        self.sqlite_conn = None
        self.pg_conn = None

    async def connect(self):
        if self.is_sqlite:
            db_path = self.database_url.replace("sqlite:///", "").replace("sqlite://", "")
            self.sqlite_conn = await aiosqlite.connect(db_path)
        else:
            self.pg_conn = await asyncpg.connect(self.database_url)

    async def disconnect(self):
        if self.sqlite_conn:
            await self.sqlite_conn.close()
        if self.pg_conn:
            await self.pg_conn.close()

    async def insert_records(self, records: list[SalaryRecord]) -> int:
        conn = self.sqlite_conn if self.is_sqlite else self.pg_conn
        return await store_records(records, conn)
