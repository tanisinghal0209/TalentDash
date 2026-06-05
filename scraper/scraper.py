# SCRAPER STATUS
# Live scraping: AmbitionBox returned HTTP 403 after ~10 requests
# and served a bot-detection challenge page.
# Detection method: checked response.status and page.title()
# for "Access Denied" / "Verify you are human" / "Robot Check"
# Mitigation attempted: random delays 1.5-4s, UA rotation (3 agents),
# realistic viewport dimensions, disabled webdriver flag
# Result: site uses JS fingerprinting (canvas, WebGL) beyond UA rotation
# For this trial: pipeline runs on mock data in data/raw_records.json
# that mirrors the exact raw format a real scraper would produce

import asyncio
import json
import random
import os
from datetime import datetime
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Standardise roles for URL building
ROLES = ["software-engineer", "data-analyst"]
PAGES_PER_ROLE = 3
MOCK_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "raw_records.json")
BLOCK_INDICATORS = ["access denied", "verify you are human", "robot check", "captcha", "403 forbidden"]

# ---------------------------------------------------------------------------
# Block detection
# ---------------------------------------------------------------------------

def is_blocked(status_code: int, page_title: str) -> bool:
    if status_code in (403, 429, 503):
        return True
    title_lower = page_title.lower()
    return any(indicator in title_lower for indicator in BLOCK_INDICATORS)

# ---------------------------------------------------------------------------
# Page scraper
# ---------------------------------------------------------------------------

async def scrape_page(page, url: str, role: str) -> list[dict]:
    records = []
    try:
        print(f"  → Navigating to: {url}")
        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        if response is None:
            print(f"  [WARN] No response object for {url}")
            return records

        page_title = await page.title()

        # Check for block
        if is_blocked(response.status, page_title):
            timestamp = datetime.utcnow().isoformat()
            print(f"  [BLOCK] {timestamp} — status={response.status} title='{page_title}'")
            print(f"  [BLOCK] Site is using bot detection. Raising BlockedError.")
            raise BlockedError(f"Blocked at {url} — status {response.status}")

        # Wait briefly for JS to render salary cards
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # AmbitionBox salary card selectors
        card_selector = "[class*='SalaryCard'], [class*='salary-card'], .salaryCard"
        cards = await page.locator(card_selector).all()

        if not cards:
            print(f"  [WARN] No cards found at {url} — selector may have changed")
            return records

        for card in cards:
            try:
                raw_company = await card.locator("[class*='companyName'], [class*='company-name']").text_content(timeout=2000)
                raw_salary_text = await card.locator("[class*='salary'], [class*='ctc']").first.text_content(timeout=2000)
                raw_location = await card.locator("[class*='location'], [class*='city']").text_content(timeout=2000)
                raw_experience = await card.locator("[class*='experience'], [class*='exp']").text_content(timeout=2000)

                records.append({
                    "raw_company": raw_company.strip() if raw_company else "",
                    "raw_role": role.replace("-", " ").title(),
                    "raw_salary_text": raw_salary_text.strip() if raw_salary_text else "",
                    "raw_location": raw_location.strip() if raw_location else "",
                    "raw_experience": raw_experience.strip() if raw_experience else "",
                })
            except Exception as e:
                print(f"  [SKIP] Failed to parse individual card: {e}")

    except BlockedError:
        raise
    except Exception as e:
        print(f"  [ERROR] Failed to retrieve page {url}: {e}")

    return records

# ---------------------------------------------------------------------------
# Fallback: load mock data
# ---------------------------------------------------------------------------

def load_mock_data() -> list[dict]:
    mock_path = os.path.abspath(MOCK_DATA_PATH)
    if not os.path.exists(mock_path):
        print(f"[FALLBACK] Mock data file not found at {mock_path}")
        print("[FALLBACK] Run mock_data_generator.py first to create it.")
        return []

    with open(mock_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"[FALLBACK] Loaded {len(records)} records from mock data file.")
    return records

# ---------------------------------------------------------------------------
# BlockedError
# ---------------------------------------------------------------------------

class BlockedError(Exception):
    pass

# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

async def run_scraper() -> list[dict]:
    all_records = []
    was_blocked = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",  # hide webdriver flag
            ],
        )

        user_agent = random.choice(USER_AGENTS)
        context = await browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )

        # Remove the navigator.webdriver flag that Playwright sets
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        try:
            for role in ROLES:
                print(f"\n[SCRAPER] Role: {role}")
                for page_num in range(1, PAGES_PER_ROLE + 1):
                    url = f"https://www.ambitionbox.com/salaries/{role}-salaries?page={page_num}"

                    try:
                        page_records = await scrape_page(page, url, role)
                        all_records.extend(page_records)
                        print(f"  [OK] Page {page_num}: got {len(page_records)} records")

                        # Polite random delay between requests
                        delay = random.uniform(1.5, 4.0)
                        print(f"  Sleeping {delay:.2f}s...")
                        await asyncio.sleep(delay)

                    except BlockedError as e:
                        print(f"\n[SCRAPER] Bot detection triggered: {e}")
                        print("[SCRAPER] Stopping live scraping. Falling back to mock data.")
                        was_blocked = True
                        break

                if was_blocked:
                    break

        finally:
            await browser.close()

    if was_blocked or not all_records:
        print("\n[SCRAPER] Live scraping did not produce usable records.")
        all_records = load_mock_data()

    return all_records

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    print("=" * 50)
    print("TALENTDASH SCRAPER")
    print("=" * 50)

    records = await run_scraper()

    # Save output
    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "raw_records.json")
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    unique_companies = len(set(r["raw_company"] for r in records if r["raw_company"]))
    print(f"\n[DONE] Scraped {len(records)} records from {unique_companies} companies.")
    print(f"[DONE] Saved to {output_path}")

if __name__ == "__main__":
    asyncio.run(main())
