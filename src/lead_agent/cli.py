from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path

from .config import AgentConfig, NicheConfig, load_config
from .db import (
    connect,
    init_db,
    list_leads_for_requalification,
    update_lead_qualification,
    upsert_lead,
)
from .env import load_dotenv
from .model import ModelInput, ModelQualificationError, qualify_with_model
from .models import LeadCandidate
from .pipeline import PipelineStats, run_single_query
from .qualify import qualify
from .scrape import ScrapeError, extract_page_signals, fetch_html
from .utils import canonical_website


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local lead generation agent")
    parser.add_argument("--dotenv-path", default=os.getenv("LEAD_AGENT_DOTENV_PATH", ".env"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="Initialize SQLite schema")
    p_init.add_argument("--db", default="data/leads.db", help="Path to SQLite database")

    p_run = sub.add_parser("run", help="Run scrape pipeline once")
    p_run.add_argument("--config", default="config/niches.example.json", help="Config file path")
    p_run.add_argument("--use-model", action="store_true", help="Use model for lead qualification")

    p_loop = sub.add_parser("loop", help="Run scrape pipeline continuously")
    p_loop.add_argument("--config", default="config/niches.example.json", help="Config file path")
    p_loop.add_argument("--interval-minutes", type=int, default=360, help="Run interval")
    p_loop.add_argument("--use-model", action="store_true", help="Use model for lead qualification")

    p_import = sub.add_parser("import-csv", help="Import existing leads CSV")
    p_import.add_argument("csv_path", help="CSV path")
    p_import.add_argument("--db", default="data/leads.db", help="Path to SQLite database")
    p_import.add_argument("--default-source", default="manual", help="source field fallback")

    p_stats = sub.add_parser("stats", help="Show lead stats")
    p_stats.add_argument("--db", default="data/leads.db", help="Path to SQLite database")

    p_ui = sub.add_parser("ui", help="Start local dashboard UI")
    p_ui.add_argument("--db", default="data/leads.db", help="Path to SQLite database")
    p_ui.add_argument("--config", default="config/niches.example.json", help="Config file path")
    p_ui.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_ui.add_argument("--port", type=int, default=8787, help="Bind port")

    p_requalify = sub.add_parser("requalify", help="Re-score existing leads from their websites")
    p_requalify.add_argument("--db", default="data/leads.db", help="Path to SQLite database")
    p_requalify.add_argument("--config", default="config/niches.example.json", help="Config file path")
    p_requalify.add_argument(
        "--all",
        action="store_true",
        help="Requalify all leads with websites (default only unscored)",
    )
    p_requalify.add_argument("--limit", type=int, default=0, help="Optional max leads to process")
    p_requalify.add_argument("--use-model", action="store_true", help="Use model for lead qualification")

    return parser.parse_args()


def _print_pipeline_summary(total: PipelineStats) -> None:
    print("Run summary:")
    print(f"  Discovered URLs: {total.discovered_urls}")
    print(f"  Processed URLs:  {total.processed_urls}")
    print(f"  Inserted leads:  {total.inserted}")
    print(f"  Updated leads:   {total.updated}")
    print(f"  Errors:          {total.errors}")


def _run_once(config: AgentConfig, use_model: bool = False) -> None:
    conn = connect(config.database_path)
    init_db(conn)
    config.model_enabled = bool(config.model_enabled or use_model)
    if config.model_enabled and not config.model_api_key:
        print("Model qualification requested but no API key is set. Falling back to heuristic.")

    total = PipelineStats()

    for niche in config.niches:
        for query in niche.queries:
            stats = run_single_query(conn, config=config, niche=niche, query=query)
            total.discovered_urls += stats.discovered_urls
            total.processed_urls += stats.processed_urls
            total.inserted += stats.inserted
            total.updated += stats.updated
            total.errors += stats.errors

    _print_pipeline_summary(total)


def _import_csv(csv_path: str, db_path: str, default_source: str) -> None:
    conn = connect(Path(db_path))
    init_db(conn)

    inserted = 0
    updated = 0

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Name") or row.get("Business") or "").strip()
            if not name:
                continue

            candidate = LeadCandidate(
                business_name=name,
                website=(row.get("Website") or "").strip(),
                email=(row.get("Email") or "").strip() or None,
                phone=(row.get("Phone") or "").strip() or None,
                area=(row.get("Area") or "").strip() or None,
                niche=(row.get("Niche") or "General").strip() or "General",
                source_url=(row.get("Website") or "").strip(),
                source_query="csv_import",
                source_type=(row.get("Source") or default_source).strip() or default_source,
                notes=(row.get("Notes") or "Imported from CSV.").strip() or "Imported from CSV.",
                website_quality=(row.get("Website Quality") or "").strip() or None,
                website_notes=(row.get("Website Notes") or "").strip() or None,
            )

            result = upsert_lead(conn, candidate)
            if result.inserted:
                inserted += 1
            else:
                updated += 1

    print(f"Imported CSV rows into {db_path}")
    print(f"  Inserted: {inserted}")
    print(f"  Updated:  {updated}")


def _show_stats(db_path: str) -> None:
    conn = connect(Path(db_path))
    init_db(conn)

    total = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
    print(f"Total leads: {total}")

    print("By niche:")
    for row in conn.execute(
        "SELECT COALESCE(niche, 'Unknown') AS niche, COUNT(*) AS n FROM leads GROUP BY 1 ORDER BY n DESC"
    ):
        print(f"  {row['niche']}: {row['n']}")

    print("By qualification band:")
    for row in conn.execute(
        "SELECT COALESCE(qualification_band, 'Unscored') AS band, COUNT(*) AS n FROM leads GROUP BY 1 ORDER BY n DESC"
    ):
        print(f"  {row['band']}: {row['n']}")


def _fallback_niche(niche_name: str | None) -> NicheConfig:
    return NicheConfig(
        name=(niche_name or "General").strip() or "General",
        queries=["requalify"],
        keywords=[],
        locations=[],
        excluded_domains=[],
    )


def _requalify(
    db_path: str, config_path: str, include_scored: bool, limit: int, use_model: bool = False
) -> None:
    config = load_config(config_path)
    config.model_enabled = bool(config.model_enabled or use_model)
    conn = connect(Path(db_path))
    init_db(conn)
    if config.model_enabled and not config.model_api_key:
        print("Model qualification requested but no API key is set. Falling back to heuristic.")

    niche_map = {n.name.lower(): n for n in config.niches}
    rows = list_leads_for_requalification(
        conn,
        only_unscored=not include_scored,
        limit=(limit if limit > 0 else None),
    )

    attempted = len(rows)
    updated = 0
    errors = 0
    skipped_invalid_website = 0

    for row in rows:
        lead_id = int(row["id"])
        website = canonical_website(str(row["website"] or ""))
        if not website:
            skipped_invalid_website += 1
            continue

        niche_name = str(row["niche"] or "General")
        niche = niche_map.get(niche_name.lower(), _fallback_niche(niche_name))

        try:
            html = fetch_html(
                url=website,
                user_agent=config.user_agent,
                timeout_seconds=config.request_timeout_seconds,
            )
            signals = extract_page_signals(html)
        except ScrapeError:
            errors += 1
            continue

        text = str(signals.get("text") or "")
        area = str(row["area"] or "").strip() or None
        if not area:
            for loc in niche.locations:
                if loc in text.lower():
                    area = loc.title()
                    break

        q = qualify(
            niche=niche,
            page_text=text,
            word_count=int(signals.get("word_count") or 0),
            has_email=bool(signals.get("email")),
            has_phone=bool(signals.get("phone")),
            inferred_area=area,
        )
        if config.model_enabled and config.model_api_key:
            try:
                q = qualify_with_model(
                    api_key=config.model_api_key,
                    model=config.model_name,
                    provider=config.model_provider,
                    timeout_seconds=config.model_timeout_seconds,
                    item=ModelInput(
                        niche=niche,
                        business_name=str(row["name"] or ""),
                        website=website,
                        area=area,
                        word_count=int(signals.get("word_count") or 0),
                        title=str(signals.get("title") or "") or None,
                        description=str(signals.get("description") or "") or None,
                        has_email=bool(signals.get("email")),
                        has_phone=bool(signals.get("phone")),
                        text_excerpt=text,
                    ),
                )
            except ModelQualificationError:
                pass

        update_lead_qualification(
            conn,
            lead_id=lead_id,
            score=q.score,
            band=q.band,
            website_quality=q.website_quality,
            website_notes=q.website_notes,
            reasons=q.reasons,
        )
        updated += 1

    print("Requalification summary:")
    print(f"  Attempted:               {attempted}")
    print(f"  Updated:                 {updated}")
    print(f"  Errors:                  {errors}")
    print(f"  Skipped invalid website: {skipped_invalid_website}")


def main() -> None:
    args = _parse_args()
    load_dotenv(args.dotenv_path)

    if args.command == "init-db":
        conn = connect(Path(args.db))
        init_db(conn)
        print(f"Initialized database at {args.db}")
        return

    if args.command == "import-csv":
        _import_csv(args.csv_path, args.db, args.default_source)
        return

    if args.command == "stats":
        _show_stats(args.db)
        return

    if args.command == "ui":
        from .dashboard import run_dashboard

        run_dashboard(
            db_path=args.db,
            config_path=args.config,
            dotenv_path=args.dotenv_path,
            host=args.host,
            port=args.port,
        )
        return

    if args.command == "requalify":
        _requalify(
            db_path=args.db,
            config_path=args.config,
            include_scored=args.all,
            limit=args.limit,
            use_model=args.use_model,
        )
        return

    if args.command == "run":
        config = load_config(args.config)
        _run_once(config, use_model=args.use_model)
        return

    if args.command == "loop":
        config = load_config(args.config)
        while True:
            _run_once(config, use_model=args.use_model)
            time.sleep(max(args.interval_minutes, 1) * 60)


if __name__ == "__main__":
    main()
