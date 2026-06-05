import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add pipeline directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from storage import Storage
from validator import SalaryRecord, TalentDashLevel
from dedup import deduplicate_existing_records, pre_insert_dedup_check

async def test_dedup():
    print("Running Database Deduplication tests...\n")
    
    # 1. Initialize temporary SQLite database
    db_url = "sqlite:///data/test_salaries.db"
    if os.path.exists("data/test_salaries.db"):
        os.remove("data/test_salaries.db")
        
    storage = Storage(db_url)
    await storage.connect()
    
    # Ensure SQLite table exists
    await storage.sqlite_conn.execute("""
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
    await storage.sqlite_conn.commit()
    
    # Define a clean base record
    record1 = SalaryRecord(
        company="google",
        company_slug="google",
        role="Software Engineer",
        level_standardized=TalentDashLevel.Mid,
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

    # Store record1
    print("Storing first record...")
    stored = await storage.insert_records([record1])
    assert stored == 1, "Should insert one record"
    
    # 2. Test pre-storage 10% deduplication (same day / 48 hours)
    # Similar record (base_salary 2,100,000 is within 5% which is also within 10%)
    record2 = SalaryRecord(
        company="google",
        company_slug="google",
        role="Software Engineer",
        level_standardized=TalentDashLevel.Mid,
        location="Bengaluru",
        currency="INR",
        experience_years=5,
        base_salary=2100000.0,
        bonus=0.0,
        stock=0.0,
        source="AmbitionBox",
        confidence_score=0.85,
        is_verified=True
    )
    
    print("Attempting to store duplicate (within 48 hours and 10% salary diff)...")
    is_dup = await pre_insert_dedup_check(record2, storage.sqlite_conn)
    assert is_dup is True, "Duplicate record check should return True"
    
    if not is_dup:
        await storage.insert_records([record2])
    print("[PASS] Pre-storage check correctly flagged the duplicate.")

    # 3. Test global database deduplication
    # To test this, we insert records with is_verified = 1 directly into SQLite, bypassing the pre-storage checks.
    # We will insert two entries for the same company + role + level, one with salary 2,000,000 and another with 2,050,000 (within 5% difference)
    # The deduplicator should flag the older one.
    
    print("\nSimulating historic database values...")
    insert_query = """
    INSERT INTO salary_records (
        id, company, company_slug, role, level_standardized, location, currency, 
        experience_years, base_salary, bonus, stock, total_compensation, source, 
        confidence_score, submitted_at, is_verified
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    
    # Historical record (older timestamp)
    old_time = (datetime.utcnow() - timedelta(days=3)).isoformat()
    await storage.sqlite_conn.execute(insert_query, (
        "old-id", "google", "google", "Software Engineer", "Mid", "Bengaluru", "INR",
        5, 2000000.0, 0.0, 0.0, 2000000.0, "AmbitionBox", 0.85, old_time, 1
    ))
    # Most recent record (current timestamp)
    now_time = datetime.utcnow().isoformat()
    await storage.sqlite_conn.execute(insert_query, (
        "new-id", "google", "google", "Software Engineer", "Mid", "Bengaluru", "INR",
        5, 2050000.0, 0.0, 0.0, 2050000.0, "AmbitionBox", 0.85, now_time, 1
    ))
    await storage.sqlite_conn.commit()

    # Run deduplication
    flagged = await deduplicate_existing_records(storage.sqlite_conn)
    print(f"Global deduplicator flagged {flagged} records.")
    
    # Verify flagged records in SQLite
    async with storage.sqlite_conn.execute("SELECT id, base_salary, is_verified FROM salary_records WHERE id IN ('old-id', 'new-id') ORDER BY submitted_at ASC;") as cursor:
        rows = await cursor.fetchall()
        for row in rows:
            print(f"Row {row[0]}: salary={row[1]}, is_verified={row[2]}")
            
        # Assert that the older duplicates got is_verified = 0, and the newest one (is_verified = 1)
        assert rows[0][0] == "old-id"
        assert rows[0][2] == 0, "Older historic record should be unverified"
        assert rows[1][0] == "new-id"
        assert rows[1][2] == 1, "Most recent record should remain verified"
        
    print("[PASS] Global database deduplicator correctly flagged duplicates.")
    await storage.disconnect()
    
    # Clean up test DB
    if os.path.exists("data/test_salaries.db"):
        os.remove("data/test_salaries.db")
    print("\nAll deduplication tests completed successfully!")

if __name__ == "__main__":
    asyncio.run(test_dedup())
