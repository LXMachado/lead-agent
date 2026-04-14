# AGENTS.md

## Purpose
This repository implements a local-first lead generation system (`lead-agent`) that discovers business websites, extracts contact signals, qualifies leads, stores them in SQLite, and exposes both CLI and local dashboard workflows.

This file is an operational handoff for multi-agent development and maintenance.

## Repository Snapshot
- Language: Python (requires `>=3.10` in `pyproject.toml`)
- Packaging: `setuptools` with `src/` layout
- Entry point: `python -m lead_agent.cli`
- Data store: SQLite (`data/leads.db` by default)
- Config: JSON (`config/niches.example.json`)
- Optional model scoring: OpenAI / Venice / OpenRouter-compatible HTTP calls

## Project Structure
- `src/lead_agent/cli.py`: CLI argument parsing and command dispatcher.
- `src/lead_agent/config.py`: JSON config + env-derived model/runtime configuration.
- `src/lead_agent/pipeline.py`: Single-query scrape pipeline orchestration.
- `src/lead_agent/discovery.py`: DuckDuckGo HTML search discovery.
- `src/lead_agent/scrape.py`: Page fetch + metadata/contact signal extraction.
- `src/lead_agent/qualify.py`: Heuristic scoring and qualification banding.
- `src/lead_agent/model.py`: Model-based qualification adapters + coercion.
- `src/lead_agent/db.py`: SQLite schema, upsert, run telemetry, qualification updates.
- `src/lead_agent/dashboard.py`: HTTP dashboard + action endpoints.
- `src/lead_agent/env.py`: lightweight dotenv loader.
- `src/lead_agent/utils.py`: URL/email/phone normalization utilities.
- `config/niches.example.json`: baseline niche/query/keyword/location config.
- `plan/example-database/...`: sample CSV and lead artifacts for imports.

## Runtime Flow
1. CLI loads env (`--dotenv-path`, default `.env`) via `load_dotenv`.
2. Config is loaded from JSON; model settings are overlaid from env.
3. Discovery queries DuckDuckGo HTML endpoint for each niche query.
4. Candidate URLs are filtered (excluded domains + social/media blockers).
5. Each URL is fetched and parsed for text/title/meta/email/phone.
6. Heuristic qualification runs; optional model may override score/band/notes.
7. Lead is upserted to `leads`; source/qualification history rows appended.
8. Run metrics are written to `scrape_runs`.

## Data Model (SQLite)
Core tables created by `init_db`:
- `leads`: latest/canonical lead snapshot.
- `lead_sources`: append-only source trace per ingest/update.
- `lead_qualifications`: append-only qualification history snapshots.
- `scrape_runs`: telemetry per niche/query execution.

Important indexes/uniqueness:
- Unique `website` when non-null.
- Unique `email` when non-null.
- Non-unique `canonical_domain` index used for dedupe fallback.

## CLI Commands
- `init-db`: initialize schema for a target DB.
- `run`: execute one discovery pass using config-defined DB path.
- `loop`: repeatedly execute `run` at interval.
- `import-csv`: import external leads into selected DB.
- `stats`: print aggregate counts by niche/band.
- `requalify`: re-score leads from website content.
- `ui`: run dashboard server.

## Environment Variables
- `OPENAI_API_KEY`: fallback model key.
- `LEAD_AGENT_MODEL_API_KEY`: preferred model key override.
- `LEAD_AGENT_USE_MODEL`: `true/false`; enables model mode by default.
- `LEAD_AGENT_MODEL`: model id.
- `LEAD_AGENT_MODEL_PROVIDER`: `openai`, `venice`, or `openrouter` (implemented in code).
- `LEAD_AGENT_MODEL_TIMEOUT_SECONDS`: model call timeout.
- `LEAD_AGENT_DOTENV_PATH`: default dotenv path for CLI flag default.

## Tested Status (2026-04-14)
The following smoke checks were executed in this workspace.

### Passed
- CLI command discovery/help works:
  - `PYTHONPATH=src ./.venv/bin/python -m lead_agent.cli --help`
- DB initialization works:
  - `... lead_agent.cli init-db --db /tmp/lead_agent_test.db`
- CSV import works:
  - `... lead_agent.cli import-csv "plan/example-database/OpenClaw Growth OS/Leads 491292f06bc0481ca2e04142cdc0b70c.csv" --db /tmp/lead_agent_test.db`
  - Result: `Inserted: 41`, `Updated: 0`.
- Stats works against imported DB:
  - `Total leads: 41`
  - Bands show `Unscored: 41`.
- Requalification command path works:
  - `... lead_agent.cli requalify --db /tmp/lead_agent_test.db --config config/niches.example.json`
  - Completed with summary output and non-zero `Errors` due inaccessible sites.
- Discovery run command executes and exits cleanly:
  - `... lead_agent.cli run --config config/niches.example.json`
  - In sandboxed environment: `Discovered 0`, `Errors 5` (network-restricted behavior).
- UI startup works out-of-sandbox:
  - `... lead_agent.cli ui --db /tmp/lead_agent_test.db --config config/niches.example.json --host 127.0.0.1 --port 18117`
  - Process stayed alive until timeout, indicating server started.

### Environment-Limited / Not Fully Verifiable Here
- Network-dependent discovery/scrape/model calls are constrained in sandbox.
- End-to-end model qualification quality was not validated against live provider responses.
- Interactive browser validation of dashboard rendering/actions was not performed in this session.

## Known Operational Notes
- `run` uses DB path from config file (`database_path`); unlike other commands, it has no `--db` flag.
- `requalify` supports `--db`, `--all`, `--limit`, and `--use-model`.
- Dashboard action buttons invoke CLI commands through subprocess using current Python executable.
- UI has port fallback logic only for `EADDRINUSE`; other bind errors bubble up.
- `canonical_website` aggressively normalizes input and can convert malformed website text into domains if they parse as hostnames.

## Multi-Agent Ownership Model
Recommended division of labor for parallel work:

1. Discovery Agent
- Owns: `discovery.py`, query quality in JSON config.
- Responsibilities: search source stability, URL extraction quality, dedupe at discovery stage, excluded-domain strategy.

2. Scrape/Signals Agent
- Owns: `scrape.py`, `utils.py` signal extraction helpers.
- Responsibilities: fetch robustness, HTML parsing quality, content-type handling, contact signal precision/recall.

3. Qualification Agent
- Owns: `qualify.py`, `model.py` scoring policy.
- Responsibilities: heuristic rubric changes, model prompt/schema tuning, provider compatibility and fallback behavior.

4. Data/Storage Agent
- Owns: `db.py`, migration/versioning strategy.
- Responsibilities: schema integrity, upsert correctness, index/performance, auditability of history tables.

5. Orchestration/CLI Agent
- Owns: `cli.py`, `pipeline.py`, execution semantics.
- Responsibilities: command UX, flag consistency, run summaries, retries/backoff strategy, operational safety.

6. UI Agent
- Owns: `dashboard.py`.
- Responsibilities: filtering, action UX, output readability, local-only security posture, responsiveness.

## Suggested Validation Matrix For Future Agents
Minimum checks before merging changes:
- Unit tests (to be added) for:
  - URL normalization and dedupe behavior.
  - qualification scoring thresholds and band boundaries.
  - model response coercion and invalid JSON handling.
  - DB upsert/update semantics and history append behavior.
- CLI smoke checks:
  - `init-db`, `import-csv`, `stats`, `requalify --limit 1`.
- Integration checks (network-enabled environment):
  - `run` with at least one query returning URLs.
  - `ui` start + action POST (`run` + `requalify`) and rendered message output.

## Risks / Gaps
- No formal automated test suite currently present.
- Live web discovery depends on third-party HTML structure (DuckDuckGo HTML endpoint may change).
- Scrape failures are expected for many websites; retry/backoff and richer error classification could improve observability.
- Model output is parsed from free-form text into JSON via regex extraction; provider response drift remains a risk.

## Quick Start For New Agents
1. Use virtualenv interpreter in repo: `./.venv/bin/python`.
2. Run with source path when package install is unavailable: prefix commands with `PYTHONPATH=src`.
3. Initialize a disposable DB for tests:
   - `PYTHONPATH=src ./.venv/bin/python -m lead_agent.cli init-db --db /tmp/lead_agent_test.db`
4. Import sample data for deterministic local checks.
5. Keep changes scoped by ownership areas above to reduce conflicts.

## Change Guidelines
- Preserve append-only behavior for `lead_sources` and `lead_qualifications`.
- Avoid destructive data operations in CLI/UI unless explicitly designed and reviewed.
- Keep CLI output stable and machine-readable enough for operator use.
- Prefer additive schema evolution with migration notes when DB structure changes.
