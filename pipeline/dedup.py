import os
import sys
import asyncio
import argparse
from datetime import datetime, timedelta
from loguru import logger
from dotenv import load_dotenv

# Thresholds as requested
SALARY_SIMILARITY_THRESHOLD = 0.10
DEDUP_WINDOW_HOURS = 48

# To support import of SalaryRecord when running from within pipeline folder
# or from project root directory
try:
    from validator import SalaryRecord
except ImportError:
    # Fallback to local import if inside pipeline folder
    sys.path.insert(0, os.path.dirname(__file__))
    from validator import SalaryRecord

def is_sqlite_conn(conn) -> bool:
    """Helper to check if the connection object is SQLite."""
    type_str = str(type(conn)).lower()
    return "sqlite" in type_str or hasattr(conn, "execute") and not hasattr(conn, "fetch")

async def pre_insert_dedup_check(record: SalaryRecord, conn) -> bool:
    """Queries the database to check if a similar record exists (base_salary within 10%)
    submitted within the last 48 hours.
    """
    company = record.company
    role = record.role
    level = record.level_standardized.value
    location = record.location
    base_salary = record.base_salary

    if is_sqlite_conn(conn):
        query = """
        SELECT id, base_salary, submitted_at 
        FROM salary_records
        WHERE company = ? 
          AND role = ? 
          AND level_standardized = ? 
          AND location = ?
          AND datetime(submitted_at) > datetime('now', '-48 hours')
          AND is_verified = 1;
        """
        async with conn.execute(query, (company, role, level, location)) as cursor:
            async for row in cursor:
                existing_id, existing_salary, submitted_at = row
                if existing_salary > 0:
                    diff = abs(existing_salary - base_salary) / existing_salary
                    if diff <= SALARY_SIMILARITY_THRESHOLD:
                        logger.info(
                            f"[DEDUP] Skipped: {company} {role} {level} — "
                            f"similar record exists from {submitted_at}"
                        )
                        return True
    else:
        query = """
        SELECT id, base_salary, submitted_at 
        FROM salary_records
        WHERE company = $1 
          AND role = $2 
          AND level_standardized = $3 
          AND location = $4
          AND submitted_at > NOW() - INTERVAL '48 hours'
          AND is_verified = TRUE;
        """
        rows = await conn.fetch(query, company, role, level, location)
        for row in rows:
            existing_salary = row["base_salary"]
            submitted_at = row["submitted_at"]
            if existing_salary > 0:
                diff = abs(existing_salary - base_salary) / existing_salary
                if diff <= SALARY_SIMILARITY_THRESHOLD:
                    logger.info(
                        f"[DEDUP] Skipped: {company} {role} {level} — "
                        f"similar record exists from {submitted_at}"
                    )
                    return True
                    
    return False

async def deduplicate_existing_records(conn) -> dict:
    """Performs a global scan on the database to identify clusters within 5% similarity
    marking older records as is_verified=False.
    """
    records = []
    is_sqlite = is_sqlite_conn(conn)

    # Fetch active records
    if is_sqlite:
        query = """
        SELECT id, company, role, level_standardized, location, base_salary, submitted_at 
        FROM salary_records WHERE is_verified = 1;
        """
        async with conn.execute(query) as cursor:
            async for row in cursor:
                records.append({
                    "id": row[0],
                    "company": row[1],
                    "role": row[2],
                    "level_standardized": row[3],
                    "location": row[4],
                    "base_salary": float(row[5]),
                    "submitted_at": row[6]
                })
    else:
        query = """
        SELECT id, company, role, level_standardized, location, base_salary, submitted_at 
        FROM salary_records WHERE is_verified = TRUE;
        """
        rows = await conn.fetch(query)
        for r in rows:
            records.append({
                "id": r["id"],
                "company": r["company"],
                "role": r["role"],
                "level_standardized": r["level_standardized"],
                "location": r["location"],
                "base_salary": float(r["base_salary"]),
                "submitted_at": r["submitted_at"]
            })

    # Group by key
    grouped = {}
    for r in records:
        key = (r["company"], r["role"], r["level_standardized"], r["location"])
        grouped.setdefault(key, []).append(r)

    groups_found = len(grouped)
    duplicates_to_flag = []

    for key, group in grouped.items():
        # Sort newest first
        # PostgreSQL uses datetime objects, SQLite stores as strings
        group.sort(key=lambda x: x["submitted_at"], reverse=True)
        
        verified = []
        for item in group:
            if not verified:
                verified.append(item)
                continue
                
            is_dup = False
            new_salary = item["base_salary"]
            
            # Check 5% similarity threshold
            for v in verified:
                v_salary = v["base_salary"]
                if v_salary > 0:
                    diff = abs(new_salary - v_salary) / v_salary
                    if diff <= 0.05:
                        is_dup = True
                        break
            if is_dup:
                duplicates_to_flag.append(item["id"])
            else:
                verified.append(item)

    # Perform updates in database
    if duplicates_to_flag:
        if is_sqlite:
            for rid in duplicates_to_flag:
                await conn.execute("UPDATE salary_records SET is_verified = 0 WHERE id = ?;", (rid,))
            await conn.commit()
        else:
            for rid in duplicates_to_flag:
                await conn.execute("UPDATE salary_records SET is_verified = FALSE WHERE id = $1;", rid)

    return {"groups_found": groups_found, "records_flagged": len(duplicates_to_flag)}

# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------

async def run_tests():
    """Runs the test inserting 3 records (2 near-duplicates within 48h, 1 older than 48h)
    and verifies that pre-insert check skips exactly the second near-duplicate.
    """
    print("Executing dedup.py unit tests...")
    import aiosqlite
    
    test_db = "data/test_dedup_direct.db"
    if os.path.exists(test_db):
        os.remove(test_db)
        
    conn = await aiosqlite.connect(test_db)
    
    # Create test table
    await conn.execute("""
    CREATE TABLE salary_records (
        id TEXT PRIMARY KEY,
        company TEXT,
        company_slug TEXT,
        role TEXT,
        level_standardized TEXT,
        location TEXT,
        currency TEXT,
        experience_years INTEGER,
        base_salary REAL,
        bonus REAL,
        stock REAL,
        total_compensation REAL,
        source TEXT,
        confidence_score REAL,
        submitted_at TEXT,
        is_verified INTEGER
    );
    """)
    await conn.commit()

    # Raw inserts directly to simulate historical and current records
    insert_query = """
    INSERT INTO salary_records (
        id, company, company_slug, role, level_standardized, location, currency, 
        experience_years, base_salary, bonus, stock, total_compensation, source, 
        confidence_score, submitted_at, is_verified
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    # Record 1: Historic record, older than 48h (e.g. 5 days ago)
    old_time = (datetime.utcnow() - timedelta(days=5)).isoformat()
    await conn.execute(insert_query, (
        "1", "google", "google", "Software Engineer", "L5", "Bengaluru", "INR",
        5, 2000000.0, 0.0, 0.0, 2000000.0, "AmbitionBox", 0.85, old_time, 1
    ))

    # Record 2: Current verified base record (just submitted)
    now_time = datetime.utcnow().isoformat()
    await conn.execute(insert_query, (
        "2", "google", "google", "Software Engineer", "L5", "Bengaluru", "INR",
        5, 2000000.0, 0.0, 0.0, 2000000.0, "AmbitionBox", 0.85, now_time, 1
    ))
    await conn.commit()

    # Define the 3rd record to insert (a near-duplicate of Record 2, within 48h, base_salary within 10%)
    record3 = SalaryRecord(
        company="google",
        company_slug="google",
        role="Software Engineer",
        level_standardized="L5",
        location="Bengaluru",
        currency="INR",
        experience_years=5,
        base_salary=2050000.0,  # 2.5% difference from 2,000,000
        bonus=0.0,
        stock=0.0,
        source="AmbitionBox",
        confidence_score=0.85,
        is_verified=True
    )

    # Perform pre-insert dedup check
    # It should match against Record 2 (submitted now, within 48h and 10% diff)
    # It should NOT be flagged as duplicate of Record 1 (since Record 1 is > 48h old)
    is_duplicate = await pre_insert_dedup_check(record3, conn)
    
    assert is_duplicate is True, "Test failed: record3 should be flagged as a duplicate of record2"
    print("[PASS] Successfully identified near-duplicate record within 48-hour window.")

    # Now let's try a record that is identical but older (this should not trigger duplicate because of age)
    record4 = SalaryRecord(
        company="google",
        company_slug="google",
        role="Software Engineer",
        level_standardized="L5",
        location="Bengaluru",
        currency="INR",
        experience_years=5,
        base_salary=2000000.0,
        bonus=0.0,
        stock=0.0,
        source="AmbitionBox",
        confidence_score=0.85,
        is_verified=True
    )
    # Since we set submitted_at on Record 1 to 5 days ago, and Record 2 is now.
    # If Record 2 didn't exist, record4 would have returned False. Since Record 2 is now, it will return True.
    # Let's delete Record 2 to verify it doesn't match Record 1 (> 48h).
    await conn.execute("DELETE FROM salary_records WHERE id = '2';")
    await conn.commit()
    
    is_duplicate_old = await pre_insert_dedup_check(record4, conn)
    assert is_duplicate_old is False, "Test failed: record4 should NOT be flagged as duplicate since record1 is too old"
    print("[PASS] Correctly ignored duplicate record outside the 48-hour window.")

    await conn.close()
    if os.path.exists(test_db):
        os.remove(test_db)
    print("All unit tests passed successfully!\n")

# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_dotenv()
    parser = argparse.ArgumentParser(description="TalentDash Deduplication cleaner tool")
    parser.add_argument("--full-scan", action="store_true", help="Run global database deduplication cleaner job")
    parser.add_argument("--test", action="store_true", help="Run unit tests for deduplication module")
    args = parser.parse_args()

    if args.full_scan:
        db_url = os.getenv("DATABASE_URL", "sqlite:///data/salaries.db")
        is_sqlite = db_url.startswith("sqlite://")
        
        async def run_cleaner():
            if is_sqlite:
                import aiosqlite
                db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
                conn = await aiosqlite.connect(db_path)
                result = await deduplicate_existing_records(conn)
                print(f"Global deduplication complete: {result}")
                await conn.close()
            else:
                import asyncpg
                conn = await asyncpg.connect(db_url)
                result = await deduplicate_existing_records(conn)
                print(f"Global deduplication complete: {result}")
                await conn.close()
                
        asyncio.run(run_cleaner())
    else:
        # Default or --test flag runs unit tests
        asyncio.run(run_tests())
