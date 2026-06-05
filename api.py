"""
TalentDash Compensation Intelligence — FastAPI Web Dashboard & API
==================================================================
Serves a premium interactive dashboard and JSON API endpoints
on top of the existing pipeline data (SQLite + rejections.jsonl).
"""

import os
import sys
import json
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "salaries.db"
REJECTIONS_PATH = BASE_DIR / "data" / "rejections.jsonl"
RAW_RECORDS_PATH = BASE_DIR / "data" / "raw_records.json"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="TalentDash Compensation Intelligence",
    description="Career intelligence pipeline — structured, comparable, decision-ready compensation data.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db():
    """Returns a SQLite connection (row_factory=Row for dict-like access)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_rejections(limit: int = 50) -> list[dict]:
    """Reads the rejections.jsonl file and returns parsed entries."""
    if not REJECTIONS_PATH.exists():
        return []
    entries = []
    with open(REJECTIONS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if len(entries) >= limit:
                break
    return entries


def load_raw_records() -> list[dict]:
    """Reads the raw_records.json file."""
    if not RAW_RECORDS_PATH.exists():
        return []
    with open(RAW_RECORDS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Health check for deployment platform."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/records")
def get_records(limit: int = 100, offset: int = 0, company: str = None, role: str = None):
    """Returns stored salary records with optional filtering."""
    try:
        conn = get_db()
        query = "SELECT * FROM salary_records WHERE is_verified = 1"
        params = []

        if company:
            query += " AND company LIKE ?"
            params.append(f"%{company}%")
        if role:
            query += " AND role LIKE ?"
            params.append(f"%{role}%")

        query += " ORDER BY total_compensation DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0]
        conn.close()

        records = [dict(r) for r in rows]
        return {"total": total, "limit": limit, "offset": offset, "records": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
def get_stats():
    """Returns pipeline statistics from the database."""
    try:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM salary_records").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0]
        companies = conn.execute(
            "SELECT COUNT(DISTINCT company) FROM salary_records"
        ).fetchone()[0]
        roles = conn.execute(
            "SELECT COUNT(DISTINCT role) FROM salary_records"
        ).fetchone()[0]
        avg_salary = conn.execute(
            "SELECT AVG(base_salary) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0]
        max_salary = conn.execute(
            "SELECT MAX(total_compensation) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0]
        min_salary = conn.execute(
            "SELECT MIN(total_compensation) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0]

        # Level distribution
        level_dist = conn.execute(
            "SELECT level_standardized, COUNT(*) as cnt FROM salary_records WHERE is_verified = 1 GROUP BY level_standardized ORDER BY cnt DESC"
        ).fetchall()

        # Company distribution
        company_dist = conn.execute(
            "SELECT company, COUNT(*) as cnt FROM salary_records WHERE is_verified = 1 GROUP BY company ORDER BY cnt DESC LIMIT 10"
        ).fetchall()

        # Location distribution
        location_dist = conn.execute(
            "SELECT location, COUNT(*) as cnt FROM salary_records WHERE is_verified = 1 GROUP BY location ORDER BY cnt DESC"
        ).fetchall()

        conn.close()

        rejections = load_rejections(limit=999)

        return {
            "total_records": total,
            "verified_records": verified,
            "unique_companies": companies,
            "unique_roles": roles,
            "avg_base_salary": round(avg_salary, 2) if avg_salary else 0,
            "max_total_compensation": max_salary or 0,
            "min_total_compensation": min_salary or 0,
            "total_rejections": len(rejections),
            "total_raw_scraped": len(load_raw_records()),
            "level_distribution": {r["level_standardized"]: r["cnt"] for r in level_dist},
            "top_companies": {r["company"]: r["cnt"] for r in company_dist},
            "location_distribution": {r["location"]: r["cnt"] for r in location_dist},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/rejections")
def get_rejections(limit: int = 20):
    """Returns rejection log entries."""
    entries = load_rejections(limit=limit)
    return {"total": len(entries), "rejections": entries}


@app.post("/api/run-pipeline")
async def run_pipeline(background_tasks: BackgroundTasks):
    """Triggers a pipeline run in rule-based (--no-llm) mode."""
    def _run():
        os.environ["NO_LLM"] = "true"
        sys.path.insert(0, str(BASE_DIR))
        sys.path.insert(0, str(BASE_DIR / "pipeline"))
        try:
            from pipeline import main_pipeline
            asyncio.run(main_pipeline(dry_run=False))
        except Exception as e:
            print(f"Pipeline run failed: {e}")

    background_tasks.add_task(_run)
    return {"status": "pipeline_triggered", "mode": "rule-based (no-llm)"}


# ---------------------------------------------------------------------------
# Dashboard (HTML)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serves the interactive HTML dashboard."""
    try:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM salary_records").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0]
        companies = conn.execute(
            "SELECT COUNT(DISTINCT company) FROM salary_records"
        ).fetchone()[0]
        roles = conn.execute(
            "SELECT COUNT(DISTINCT role) FROM salary_records"
        ).fetchone()[0]
        avg_salary = conn.execute(
            "SELECT AVG(base_salary) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0] or 0
        max_comp = conn.execute(
            "SELECT MAX(total_compensation) FROM salary_records WHERE is_verified = 1"
        ).fetchone()[0] or 0

        # Top records
        top_records = conn.execute(
            "SELECT company, role, level_standardized, location, base_salary, bonus, stock, total_compensation, confidence_score FROM salary_records WHERE is_verified = 1 ORDER BY total_compensation DESC LIMIT 15"
        ).fetchall()

        # Records for comparison dropdowns
        compare_rows = conn.execute(
            "SELECT company, role, level_standardized, location, experience_years, base_salary, bonus, stock, total_compensation FROM salary_records WHERE is_verified = 1 ORDER BY total_compensation DESC LIMIT 50"
        ).fetchall()
        compare_records_json = json.dumps([dict(r) for r in compare_rows])

        # Level dist
        level_dist = conn.execute(
            "SELECT level_standardized, COUNT(*) as cnt FROM salary_records WHERE is_verified = 1 GROUP BY level_standardized ORDER BY cnt DESC"
        ).fetchall()

        # Company dist
        company_dist = conn.execute(
            "SELECT company, COUNT(*) as cnt, ROUND(AVG(base_salary)) as avg_sal FROM salary_records WHERE is_verified = 1 GROUP BY company ORDER BY avg_sal DESC"
        ).fetchall()

        location_dist = conn.execute(
            "SELECT location, COUNT(*) as cnt FROM salary_records WHERE is_verified = 1 GROUP BY location ORDER BY cnt DESC"
        ).fetchall()

        conn.close()
    except Exception:
        total = verified = companies = roles = 0
        avg_salary = max_comp = 0
        top_records = []
        compare_records_json = "[]"
        level_dist = []
        company_dist = []
        location_dist = []

    rejections = load_rejections(limit=10)
    raw_count = len(load_raw_records())
    rejected_count = len(load_rejections(limit=999))

    # Build table rows
    records_html = ""
    for r in top_records:
        conf_color = "#4ade80" if r["confidence_score"] >= 0.7 else "#fbbf24" if r["confidence_score"] >= 0.4 else "#f87171"
        records_html += f"""
        <tr>
            <td>{r['company']}</td>
            <td>{r['role']}</td>
            <td><span class="badge badge-level">{r['level_standardized']}</span></td>
            <td>{r['location']}</td>
            <td class="num">₹{r['base_salary']:,.0f}</td>
            <td class="num">₹{r['bonus']:,.0f}</td>
            <td class="num">₹{r['stock']:,.0f}</td>
            <td class="num total">₹{r['total_compensation']:,.0f}</td>
            <td><span class="conf-dot" style="background:{conf_color}"></span> {r['confidence_score']:.2f}</td>
        </tr>"""

    level_bars = ""
    if level_dist:
        max_cnt = max(r["cnt"] for r in level_dist)
        for r in level_dist:
            pct = (r["cnt"] / max_cnt) * 100 if max_cnt else 0
            level_bars += f"""
            <div class="bar-row">
                <span class="bar-label">{r['level_standardized']}</span>
                <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
                <span class="bar-val">{r['cnt']}</span>
            </div>"""

    company_bars = ""
    for r in company_dist:
        company_bars += f"""
        <div class="company-row">
            <span class="company-name">{r['company']}</span>
            <span class="company-cnt">{r['cnt']} records</span>
            <span class="company-avg">Avg ₹{r['avg_sal']:,.0f}</span>
        </div>"""

    location_tags = ""
    for r in location_dist:
        location_tags += f'<span class="loc-tag">{r["location"]} <b>{r["cnt"]}</b></span>'

    rejection_rows = ""
    for rej in rejections:
        raw = rej.get("raw_input", {})
        reason_short = rej.get("rejection_reason", "")[:120]
        rejection_rows += f"""
        <tr>
            <td>{raw.get('raw_company', '—')}</td>
            <td>{raw.get('raw_role', '—')}</td>
            <td>{raw.get('raw_salary_text', '—')}</td>
            <td class="reason">{reason_short}…</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TalentDash — Compensation Intelligence Dashboard</title>
<meta name="description" content="TalentDash: structured, comparable, decision-ready career compensation data for Indian tech roles.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
:root {{
  --bg: #F7F7F7;
  --surface: #FFFFFF;
  --surface-2: #FFFFFF;
  --border: #EBEBEB;
  --text: #222222;
  --text-body: #484848;
  --text-dim: #717171;
  --accent: #FF5A5F;
  --accent-glow: rgba(255,90,95,0.1);
  --green: #008A05;
  --amber: #FFB400;
  --red: #D93025;
  --hover: #F2F2F2;
  --cyan: #FF5A5F; /* Fallback for existing tags */
  --radius: 12px;
}}
body {{
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text-body);
  line-height: 1.6;
  min-height: 100vh;
}}
h1, h2, h3, .logo, .stat-value, .company-name {{ color: var(--text); }}
.container {{ max-width: 1280px; margin: 0 auto; padding: 2rem 1.5rem; }}

/* Header */
.header {{
  text-align: center;
  padding: 3rem 0 2rem;
  position: relative;
}}
.logo {{ font-size: 36px; font-weight: 700; line-height: 1.1; letter-spacing: -1px; color: var(--text); }}
.logo span {{ color: var(--accent); }}
.subtitle {{ color: var(--text-dim); font-size: 16px; margin-top: 0.5rem; font-weight: 400; }}

/* Stat Cards */
.stats-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1.25rem;
  margin: 2rem 0;
}}
.stat-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  transition: transform 0.2s, box-shadow 0.2s;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  overflow: hidden;
}}
.stat-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
.stat-label {{ font-size: 13px; font-weight: 500; color: var(--text-dim); margin-bottom: 0.4rem; }}
.stat-value {{ font-size: 28px; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.stat-value.accent {{ color: var(--accent); }}
.stat-value.green {{ color: var(--green); }}
.stat-value.amber {{ color: var(--amber); }}
.stat-value.red {{ color: var(--red); }}
.stat-value.cyan {{ color: var(--text); }}

/* Pipeline Flow */
.pipeline-section {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2rem;
  margin: 2rem 0;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}}
.pipeline-flow {{
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 1rem 0;
}}
.pipe-step {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 1rem;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.2s;
  color: var(--text);
}}
.pipe-step:hover {{ background: var(--hover); }}
.pipe-arrow {{ color: var(--text-dim); font-size: 1.2rem; font-weight: 700; }}

/* Section titles */
.section-title {{
  font-size: 22px;
  font-weight: 600;
  color: var(--text);
  margin: 2.5rem 0 1.5rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}}

/* Table */
.table-wrap {{
  overflow-x: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}}
table {{ width: 100%; border-collapse: collapse; font-size: 16px; }}
th {{ background: var(--surface); color: var(--text-dim); font-size: 12px; font-weight: 500; padding: 1rem; text-align: left; position: sticky; top: 0; border-bottom: 1px solid var(--border); }}
td {{ padding: 1rem; border-top: 1px solid var(--border); color: var(--text-body); }}
tr:hover td {{ background: var(--hover); }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.total {{ color: var(--text); font-weight: 600; }}
.badge {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 13px; font-weight: 500; }}
.badge-level {{ background: var(--accent-glow); color: var(--accent); }}
.conf-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }}
.reason {{ font-size: 13px; color: var(--text-dim); max-width: 300px; overflow: hidden; text-overflow: ellipsis; }}

/* Bars */
.bars-section {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin: 1rem 0 2rem;
}}
@media (max-width: 768px) {{ .bars-section {{ grid-template-columns: 1fr; }} }}
.bars-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}}
.bars-card h3 {{ font-size: 13px; font-weight: 500; color: var(--text-dim); margin-bottom: 1.5rem; text-transform: uppercase; letter-spacing: 0.5px; }}
.bar-row {{ display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.8rem; }}
.bar-label {{ width: 100px; font-size: 13px; font-weight: 500; text-align: right; color: var(--text); }}
.bar-track {{ flex: 1; height: 12px; background: var(--bg); border-radius: 6px; overflow: hidden; }}
.bar-fill {{ height: 100%; background: var(--accent); border-radius: 6px; transition: width 0.6s ease; }}
.bar-val {{ font-size: 13px; color: var(--text-dim); width: 30px; }}

/* Company rows */
.company-row {{ display: flex; align-items: center; justify-content: space-between; padding: 0.8rem 0; border-bottom: 1px solid var(--border); }}
.company-row:last-child {{ border-bottom: none; }}
.company-name {{ font-weight: 600; font-size: 16px; color: var(--text); }}
.company-cnt {{ color: var(--text-dim); font-size: 13px; }}
.company-avg {{ color: var(--text); font-size: 16px; font-weight: 600; }}

/* Location tags */
.loc-tags {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; }}
.loc-tag {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 0.4rem 1rem;
  font-size: 13px;
  color: var(--text-body);
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}}
.loc-tag b {{ color: var(--text); margin-left: 4px; font-weight: 600; }}

/* API section */
.api-section {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem 2rem;
  margin: 2rem 0;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}}
.api-endpoint {{
  display: flex;
  align-items: center;
  gap: 0.8rem;
  padding: 1rem 0;
  border-bottom: 1px solid var(--border);
}}
.api-endpoint:last-child {{ border-bottom: none; }}
.method {{
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  font-family: monospace;
}}
.method-get {{ background: rgba(0,138,5,0.1); color: var(--green); }}
.method-post {{ background: rgba(255,180,0,0.1); color: var(--amber); }}
.api-path {{ font-family: monospace; font-size: 14px; font-weight: 500; color: var(--text); }}
.api-desc {{ color: var(--text-dim); font-size: 13px; margin-left: auto; }}

/* Compare UI */
.compare-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  margin-bottom: 2rem;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}}
.compare-selectors {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}}
.compare-box {{
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  background: var(--bg);
}}
.compare-box select {{
  width: 100%;
  padding: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  margin-top: 0.5rem;
}}
.compare-label {{
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  display: flex;
  justify-content: space-between;
}}
.winner-badge {{
  background: #005A9C;
  color: white;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 700;
}}
.compare-table {{
  width: 100%;
  border-collapse: collapse;
}}
.compare-table th {{ background: transparent; color: var(--text-dim); font-size: 12px; border-bottom: 1px solid var(--border); padding: 1rem; text-align: left; }}
.compare-table td {{ padding: 1rem; border-top: 1px solid var(--border); color: var(--text-body); }}
.compare-table tr:hover td {{ background: var(--hover); }}
.delta-pos {{ color: var(--green); font-weight: 600; }}
.delta-neg {{ color: var(--red); font-weight: 600; }}
.delta-zero {{ color: var(--text-dim); }}

/* Footer */
.footer {{
  text-align: center;
  padding: 2rem 0;
  color: var(--text-dim);
  font-size: 13px;
  border-top: 1px solid var(--border);
  margin-top: 3rem;
}}
</style>
</head>
<body>

<div class="container">

  <!-- Header -->
  <div class="header">
    <div class="logo"><span>TalentDash</span></div>
    <p class="subtitle">Compensation Intelligence Pipeline — Structured, Comparable, Decision-Ready Data</p>
  </div>

  <!-- Pipeline Flow -->
  <div class="pipeline-section">
    <div class="pipeline-flow">
      <div class="pipe-step">Scraper</div>
      <span class="pipe-arrow">→</span>
      <div class="pipe-step">Normaliser</div>
      <span class="pipe-arrow">→</span>
      <div class="pipe-step">Company Clean</div>
      <span class="pipe-arrow">→</span>
      <div class="pipe-step">Level Mapper</div>
      <span class="pipe-arrow">→</span>
      <div class="pipe-step">Pydantic Validator</div>
      <span class="pipe-arrow">→</span>
      <div class="pipe-step">Dedup Check</div>
      <span class="pipe-arrow">→</span>
      <div class="pipe-step">Storage</div>
    </div>
  </div>

  <!-- Stats Grid -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Raw Scraped</div>
      <div class="stat-value cyan">{raw_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Validated & Stored</div>
      <div class="stat-value green">{verified}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Rejected</div>
      <div class="stat-value red">{rejected_count}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Companies</div>
      <div class="stat-value accent">{companies}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Unique Roles</div>
      <div class="stat-value accent">{roles}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Avg Base Salary</div>
      <div class="stat-value green">₹{avg_salary:,.0f}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Max Total Comp</div>
      <div class="stat-value amber">₹{max_comp:,.0f}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Pipeline Pass Rate</div>
      <div class="stat-value green">{(verified/raw_count*100) if raw_count else 0:.0f}%</div>
    </div>
  </div>

  <!-- Distribution Charts -->
  <div class="section-title"><span class="dot"></span> Distributions</div>
  <div class="bars-section">
    <div class="bars-card">
      <h3>By Level</h3>
      {level_bars}
    </div>
    <div class="bars-card">
      <h3>By Company (Avg Salary)</h3>
      {company_bars}
    </div>
  </div>

  <!-- Location Tags -->
  <div class="section-title"><span class="dot"></span> Locations</div>
  <div class="loc-tags" style="margin-bottom: 2rem;">
    {location_tags}
  </div>

  <!-- Compare Salaries UI -->
  <div class="section-title"><span class="dot"></span> Compare Salaries</div>
  <p style="color:var(--text-dim); margin-top:-1rem; margin-bottom:1.5rem; font-size:14px;">Select two salary records to see a side-by-side breakdown with deltas.</p>
  <div class="compare-container">
    <div class="compare-selectors">
      <div class="compare-box" id="boxA">
        <div class="compare-label">RECORD A <span id="winnerA" class="winner-badge" style="display:none;">Higher TC</span></div>
        <select id="selectA"></select>
      </div>
      <div class="compare-box" id="boxB">
        <div class="compare-label">RECORD B <span id="winnerB" class="winner-badge" style="display:none;">Higher TC</span></div>
        <select id="selectB"></select>
      </div>
    </div>
    
    <table class="compare-table" id="compareTable">
      <thead>
        <tr>
          <th>FIELD</th>
          <th>RECORD A <span id="colWinnerA" class="winner-badge" style="display:none;">WINNER</span></th>
          <th>RECORD B <span id="colWinnerB" class="winner-badge" style="display:none;">WINNER</span></th>
          <th>DELTA</th>
        </tr>
      </thead>
      <tbody>
        <!-- Filled by JS -->
      </tbody>
    </table>
  </div>

  <!-- Records Table -->
  <div class="section-title"><span class="dot"></span> Top Salary Records</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Company</th>
          <th>Role</th>
          <th>Level</th>
          <th>Location</th>
          <th style="text-align:right">Base Salary</th>
          <th style="text-align:right">Bonus</th>
          <th style="text-align:right">Stock</th>
          <th style="text-align:right">Total Comp</th>
          <th>Confidence</th>
        </tr>
      </thead>
      <tbody>
        {records_html}
      </tbody>
    </table>
  </div>

  <!-- Rejections -->
  <div class="section-title"><span class="dot"></span> Recent Rejections ({rejected_count} total)</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Company</th>
          <th>Role</th>
          <th>Salary Text</th>
          <th>Rejection Reason</th>
        </tr>
      </thead>
      <tbody>
        {rejection_rows if rejection_rows else '<tr><td colspan="4" style="text-align:center;color:var(--text-dim)">No rejections logged yet</td></tr>'}
      </tbody>
    </table>
  </div>

  <!-- API Endpoints -->
  <div class="section-title"><span class="dot"></span> API Endpoints</div>
  <div class="api-section">
    <div class="api-endpoint">
      <span class="method method-get">GET</span>
      <span class="api-path">/api/records</span>
      <span class="api-desc">All stored salary records (supports ?company= &amp; ?role= filters)</span>
    </div>
    <div class="api-endpoint">
      <span class="method method-get">GET</span>
      <span class="api-path">/api/stats</span>
      <span class="api-desc">Pipeline statistics &amp; distribution data</span>
    </div>
    <div class="api-endpoint">
      <span class="method method-get">GET</span>
      <span class="api-path">/api/rejections</span>
      <span class="api-desc">Validation rejection log entries</span>
    </div>
    <div class="api-endpoint">
      <span class="method method-post">POST</span>
      <span class="api-path">/api/run-pipeline</span>
      <span class="api-desc">Trigger a new pipeline run (rule-based mode)</span>
    </div>
    <div class="api-endpoint">
      <span class="method method-get">GET</span>
      <span class="api-path">/health</span>
      <span class="api-desc">Health check</span>
    </div>
    <div class="api-endpoint">
      <span class="method method-get">GET</span>
      <span class="api-path">/docs</span>
      <span class="api-desc">Interactive Swagger API documentation (auto-generated)</span>
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <p>TalentDash Compensation Intelligence Pipeline &middot; AI &amp; Data Engineering Trial &middot; Built with FastAPI + SQLite</p>
  </div>

</div>

<script>
  const compareRecords = {compare_records_json};
  const selectA = document.getElementById('selectA');
  const selectB = document.getElementById('selectB');
  const tbody = document.querySelector('#compareTable tbody');

  function formatMoney(val) {{
      if (val >= 10000000) return '₹' + (val / 10000000).toFixed(2) + ' Cr';
      if (val >= 100000) return '₹' + (val / 100000).toFixed(2) + ' L';
      return '₹' + val.toLocaleString('en-IN');
  }}

  function formatDelta(diff, isText=false) {{
      if (isText) return diff === 0 ? '—' : (diff > 0 ? '+' + diff : diff);
      if (diff === 0) return '—';
      const sign = diff > 0 ? '+' : '-';
      const absDiff = Math.abs(diff);
      return sign + formatMoney(absDiff);
  }}

  function getDeltaClass(diff) {{
      if (diff === 0) return 'delta-zero';
      return diff > 0 ? 'delta-pos' : 'delta-neg';
  }}

  function initCompare() {{
      if (!compareRecords || compareRecords.length === 0) return;
      
      compareRecords.forEach((r, i) => {{
          const label = `${{r.company}} — ${{r.role}} (${{r.level_standardized}}) — ${{formatMoney(r.total_compensation)}}`;
          selectA.add(new Option(label, i));
          selectB.add(new Option(label, i));
      }});
      
      if (compareRecords.length > 1) selectB.selectedIndex = 1;

      selectA.addEventListener('change', updateCompare);
      selectB.addEventListener('change', updateCompare);
      updateCompare();
  }}

  function updateCompare() {{
      const rA = compareRecords[selectA.value];
      const rB = compareRecords[selectB.value];
      if (!rA || !rB) return;

      const tcDiff = rA.total_compensation - rB.total_compensation;
      document.getElementById('winnerA').style.display = tcDiff > 0 ? 'inline-block' : 'none';
      document.getElementById('colWinnerA').style.display = tcDiff > 0 ? 'inline-block' : 'none';
      document.getElementById('winnerB').style.display = tcDiff < 0 ? 'inline-block' : 'none';
      document.getElementById('colWinnerB').style.display = tcDiff < 0 ? 'inline-block' : 'none';
      
      document.getElementById('boxA').style.borderColor = tcDiff > 0 ? '#005A9C' : 'var(--border)';
      document.getElementById('boxB').style.borderColor = tcDiff < 0 ? '#005A9C' : 'var(--border)';

      const rows = [
          ['Company', `<b>${{rA.company}}</b>`, `<b>${{rB.company}}</b>`, '—'],
          ['Role', rA.role, rB.role, '—'],
          ['Level', `<span class="badge badge-level">${{rA.level_standardized}}</span>`, `<span class="badge badge-level">${{rB.level_standardized}}</span>`, '—'],
          ['Location', rA.location, rB.location, '—'],
          ['Experience', `${{rA.experience_years}} years`, `${{rB.experience_years}} years`, `<span class="${{getDeltaClass(rA.experience_years - rB.experience_years)}}">${{formatDelta(rA.experience_years - rB.experience_years, true)}}</span>`],
          ['Base Salary', formatMoney(rA.base_salary), formatMoney(rB.base_salary), `<span class="${{getDeltaClass(rA.base_salary - rB.base_salary)}}">${{formatDelta(rA.base_salary - rB.base_salary)}}</span>`],
          ['Bonus', formatMoney(rA.bonus), formatMoney(rB.bonus), `<span class="${{getDeltaClass(rA.bonus - rB.bonus)}}">${{formatDelta(rA.bonus - rB.bonus)}}</span>`],
          ['Stock / RSU', formatMoney(rA.stock), formatMoney(rB.stock), `<span class="${{getDeltaClass(rA.stock - rB.stock)}}">${{formatDelta(rA.stock - rB.stock)}}</span>`],
          ['Total Comp', `<b style="color:#005A9C">${{formatMoney(rA.total_compensation)}}</b>`, `<b style="color:#005A9C">${{formatMoney(rB.total_compensation)}}</b>`, `<span class="${{getDeltaClass(tcDiff)}}">${{formatDelta(tcDiff)}}</span>`]
      ];

      tbody.innerHTML = rows.map(r => `<tr><td>${{r[0]}}</td><td>${{r[1]}}</td><td>${{r[2]}}</td><td>${{r[3]}}</td></tr>`).join('');
  }}

  document.addEventListener('DOMContentLoaded', initCompare);
</script>

</body>
</html>"""

    return HTMLResponse(content=html)
