import sys
import os
import asyncio
import json
from dotenv import load_dotenv
from loguru import logger

# Insert the parent directory and pipeline directory into sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

from scraper.scraper import run_scraper
from normaliser import normalize_batch
from normalise_company import normalize_company
from level_mapper import LevelMapper
from validator import run_validation_pipeline
from dedup import pre_insert_dedup_check, deduplicate_existing_records
from storage import store_records
from quality_report import PipelineStats, print_quality_report

# Load environment variables
load_dotenv()

async def main_pipeline(dry_run: bool = False):
    logger.info("Starting TalentDash End-to-End Pipeline...")
    stats = PipelineStats()

    # Stage 1: Scraper
    try:
        logger.info("Stage 1: Running Scraper...")
        raw_records = await run_scraper()
        stats.total_scraped = len(raw_records)
        stats.raw_records = raw_records.copy()
        logger.info(f"Scraper returned {len(raw_records)} raw records.")
    except Exception as e:
        logger.error(f"Scraper stage failed: {e}")
        sys.exit(1)

    if not raw_records:
        logger.warning("No records scraped. Pipeline exiting.")
        return

    # Stage 2: Normalisation
    try:
        logger.info("Stage 2: Normalising raw records batch...")
        normalized_batch = normalize_batch(raw_records, stats)
        logger.info(f"Normalisation complete: {stats.passed_normalisation} records processed.")
    except Exception as e:
        logger.error(f"Normalisation stage failed: {e}")
        sys.exit(1)

    # Stage 3 & 4: Company name normalisation & Level mapping
    try:
        logger.info("Stage 3 & 4: Normalising company names and mapping levels...")
        level_mapper = LevelMapper()
        
        for record in normalized_batch:
            # Clean company name and get slug
            raw_company = record.get("raw_company", "")
            canonical_company, company_slug = normalize_company(raw_company)
            record["company"] = canonical_company
            record["company_slug"] = company_slug
            
            # Map role and experience to level
            raw_role = record.get("raw_role", "")
            record["role"] = raw_role
            exp_years = record.get("experience_years")
            level, level_confidence = level_mapper.map_level(raw_role, exp_years)
            record["level_standardized"] = level
            
            # Set confidence score
            record["confidence_score"] = level_confidence
            
    except Exception as e:
        logger.error(f"Company normalisation / level mapping stage failed: {e}")
        sys.exit(1)

    # Stage 5: Validation Pipeline
    try:
        logger.info("Stage 5: Running Pydantic Validation pipeline...")
        validated_records = run_validation_pipeline(normalized_batch, stats)
        logger.info(f"Validation complete: {stats.passed_pydantic} passed, {stats.rejected_total} rejected.")
    except Exception as e:
        logger.error(f"Validation stage failed: {e}")
        sys.exit(1)

    # Stage 6 & 7: Deduplication and Storage
    if dry_run:
        logger.info("--- DRY RUN MODE ACTIVATED ---")
        logger.info("Skipping database connections, deduplication, and insertion.")
        
        # Simulate stored success count in dry run
        stats.stored_success = len(validated_records)
        
        sample_size = min(3, len(validated_records))
        print(f"\n[DRY RUN] Would insert {len(validated_records)} records. First {sample_size} records:")
        for idx in range(sample_size):
            print(f"  Record #{idx+1}: {validated_records[idx].model_dump()}")
        print("-" * 50 + "\n")
    else:
        try:
            logger.info("Stage 6 & 7: Database connection, Deduplication checks, and Storage...")
            db_url = os.getenv("DATABASE_URL", "sqlite:///data/salaries.db")
            is_sqlite = db_url.startswith("sqlite://")
            
            # Connect to database to check duplicates and store
            if is_sqlite:
                import aiosqlite
                db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
                if os.path.dirname(db_path):
                    os.makedirs(os.path.dirname(db_path), exist_ok=True)
                conn = await aiosqlite.connect(db_path)
                
                # Ensure SQLite table matches PostgreSQL types
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
                import asyncpg
                conn = await asyncpg.connect(db_url)
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

            non_duplicates = []
            for record in validated_records:
                # Check duplication before insertion
                is_duplicate = await pre_insert_dedup_check(record, conn)
                if is_duplicate:
                    stats.duplicates_skipped += 1
                else:
                    non_duplicates.append(record)

            # Store remaining records
            stored_count = await store_records(non_duplicates, conn)
            stats.stored_success = stored_count
            
            await conn.close()
            
        except Exception as e:
            logger.error(f"Deduplication / Storage stage failed: {e}")
            sys.exit(1)

    # Stage 8: Quality Report
    logger.info("Stage 8: Generating Quality Report...")
    print_quality_report(stats)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TalentDash End-to-End Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Runs pipeline without modifying the database")
    parser.add_argument("--dedup", action="store_true", help="Run global database deduplication cleaner job")
    parser.add_argument("--no-llm", action="store_true", help="Disables LLM processing and runs in rule-based mode")
    args = parser.parse_args()

    if args.no_llm:
        os.environ["NO_LLM"] = "true"

    if args.dedup:
        async def run_cleaner():
            load_dotenv()
            db_url = os.getenv("DATABASE_URL", "sqlite:///data/salaries.db")
            is_sqlite = db_url.startswith("sqlite://")
            
            if is_sqlite:
                import aiosqlite
                db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
                conn = await aiosqlite.connect(db_path)
                result = await deduplicate_existing_records(conn)
                print(f"Deduplication cleaner complete: {result}")
                await conn.close()
            else:
                import asyncpg
                conn = await asyncpg.connect(db_url)
                result = await deduplicate_existing_records(conn)
                print(f"Deduplication cleaner complete: {result}")
                await conn.close()
        asyncio.run(run_cleaner())
    else:
        asyncio.run(main_pipeline(dry_run=args.dry_run))
