from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import LeadCandidate, UpsertResult
from .utils import canonical_website, normalize_email, normalize_phone, website_domain


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            business TEXT,
            website TEXT,
            canonical_domain TEXT,
            email TEXT,
            phone TEXT,
            area TEXT,
            niche TEXT,
            website_quality TEXT,
            website_notes TEXT,
            notes TEXT,
            next_step TEXT,
            source TEXT,
            status TEXT DEFAULT 'New',
            last_contact_date TEXT,
            qualification_score INTEGER,
            qualification_band TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_website ON leads(website) WHERE website IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email ON leads(email) WHERE email IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(canonical_domain);
        CREATE INDEX IF NOT EXISTS idx_leads_niche ON leads(niche);
        CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

        CREATE TABLE IF NOT EXISTS lead_sources (
            id INTEGER PRIMARY KEY,
            lead_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT,
            source_query TEXT,
            observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );

        CREATE INDEX IF NOT EXISTS idx_lead_sources_lead_id ON lead_sources(lead_id);

        CREATE TABLE IF NOT EXISTS lead_qualifications (
            id INTEGER PRIMARY KEY,
            lead_id INTEGER NOT NULL,
            score INTEGER,
            band TEXT,
            reasons_json TEXT,
            website_quality TEXT,
            website_notes TEXT,
            method TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );

        CREATE INDEX IF NOT EXISTS idx_lead_qualifications_lead_id ON lead_qualifications(lead_id);

        CREATE TABLE IF NOT EXISTS scrape_runs (
            id INTEGER PRIMARY KEY,
            niche TEXT NOT NULL,
            query TEXT NOT NULL,
            discovered_urls INTEGER DEFAULT 0,
            processed_urls INTEGER DEFAULT 0,
            inserted_leads INTEGER DEFAULT 0,
            updated_leads INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        );
        """
    )
    conn.commit()


def start_run(conn: sqlite3.Connection, niche: str, query: str) -> int:
    cur = conn.execute(
        "INSERT INTO scrape_runs (niche, query) VALUES (?, ?)",
        (niche, query),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    discovered_urls: int,
    processed_urls: int,
    inserted: int,
    updated: int,
    errors: int,
) -> None:
    conn.execute(
        """
        UPDATE scrape_runs
        SET discovered_urls = ?,
            processed_urls = ?,
            inserted_leads = ?,
            updated_leads = ?,
            errors = ?,
            finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (discovered_urls, processed_urls, inserted, updated, errors, run_id),
    )
    conn.commit()


def upsert_lead(conn: sqlite3.Connection, candidate: LeadCandidate) -> UpsertResult:
    website = canonical_website(candidate.website)
    domain = website_domain(website)
    email = normalize_email(candidate.email)
    phone = normalize_phone(candidate.phone)

    row = None
    if website:
        row = conn.execute("SELECT id FROM leads WHERE website = ?", (website,)).fetchone()
    if row is None and email:
        row = conn.execute("SELECT id FROM leads WHERE email = ?", (email,)).fetchone()
    if row is None and domain:
        row = conn.execute(
            "SELECT id FROM leads WHERE canonical_domain = ? ORDER BY id DESC LIMIT 1", (domain,)
        ).fetchone()

    if row is None:
        cur = conn.execute(
            """
            INSERT INTO leads (
                name, business, website, canonical_domain, email, phone, area, niche,
                website_quality, website_notes, notes, source, qualification_score, qualification_band
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.business_name,
                candidate.business_name,
                website,
                domain,
                email,
                phone,
                candidate.area,
                candidate.niche,
                candidate.website_quality,
                candidate.website_notes,
                candidate.notes,
                candidate.source_type,
                candidate.qualification_score,
                candidate.qualification_band,
            ),
        )
        lead_id = int(cur.lastrowid)
        inserted = True
    else:
        lead_id = int(row["id"])
        conn.execute(
            """
            UPDATE leads
            SET
              name = COALESCE(name, ?),
              business = COALESCE(business, ?),
              website = COALESCE(website, ?),
              canonical_domain = COALESCE(canonical_domain, ?),
              email = COALESCE(email, ?),
              phone = COALESCE(phone, ?),
              area = COALESCE(area, ?),
              niche = COALESCE(niche, ?),
              website_quality = COALESCE(website_quality, ?),
              website_notes = COALESCE(website_notes, ?),
              qualification_score = COALESCE(qualification_score, ?),
              qualification_band = COALESCE(qualification_band, ?),
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                candidate.business_name,
                candidate.business_name,
                website,
                domain,
                email,
                phone,
                candidate.area,
                candidate.niche,
                candidate.website_quality,
                candidate.website_notes,
                candidate.qualification_score,
                candidate.qualification_band,
                lead_id,
            ),
        )
        inserted = False

    conn.execute(
        """
        INSERT INTO lead_sources (lead_id, source_type, source_url, source_query)
        VALUES (?, ?, ?, ?)
        """,
        (lead_id, candidate.source_type, candidate.source_url, candidate.source_query),
    )

    conn.execute(
        """
        INSERT INTO lead_qualifications (
            lead_id, score, band, reasons_json, website_quality, website_notes, method
        ) VALUES (?, ?, ?, ?, ?, ?, 'heuristic-v1')
        """,
        (
            lead_id,
            candidate.qualification_score,
            candidate.qualification_band,
            json.dumps(candidate.qualification_reasons, ensure_ascii=True),
            candidate.website_quality,
            candidate.website_notes,
        ),
    )

    conn.commit()
    return UpsertResult(inserted=inserted, lead_id=lead_id)


def list_leads_for_requalification(
    conn: sqlite3.Connection, only_unscored: bool = True, limit: int | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT id, name, website, area, niche
        FROM leads
        WHERE website IS NOT NULL
          AND TRIM(website) <> ''
    """
    params: list[int] = []

    if only_unscored:
        sql += " AND qualification_band IS NULL"

    sql += " ORDER BY id ASC"

    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    return list(conn.execute(sql, tuple(params)).fetchall())


def update_lead_qualification(
    conn: sqlite3.Connection,
    lead_id: int,
    score: int,
    band: str,
    website_quality: str,
    website_notes: str,
    reasons: list[str],
    method: str = "heuristic-v1-requalify",
) -> None:
    conn.execute(
        """
        UPDATE leads
        SET
          qualification_score = ?,
          qualification_band = ?,
          website_quality = ?,
          website_notes = ?,
          updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (score, band, website_quality, website_notes, lead_id),
    )

    conn.execute(
        """
        INSERT INTO lead_qualifications (
            lead_id, score, band, reasons_json, website_quality, website_notes, method
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            score,
            band,
            json.dumps(reasons, ensure_ascii=True),
            website_quality,
            website_notes,
            method,
        ),
    )
    conn.commit()
