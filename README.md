# Lead Agent (Local v1)

Local-first lead generation system that discovers businesses, extracts contact signals, qualifies leads, and stores everything in SQLite.

## Features
- Web discovery from niche queries
- Contact signal extraction (email, phone, website)
- Qualification and categorization (`Hot`, `Warm`, `Cold`, `Unscored`)
- Optional model-based scoring (OpenAI-compatible providers, including Venice)
- Append-only history tables for auditability
- Local dashboard UI for filtering and running actions
- No delete operations in pipeline or UI

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Environment Variables
Create `.env` from template:
```bash
cp .env.example .env
```

Common variables:
- `OPENAI_API_KEY`: default model API key
- `LEAD_AGENT_MODEL_API_KEY`: overrides `OPENAI_API_KEY` when set
- `LEAD_AGENT_USE_MODEL`: `true/false` default model usage
- `LEAD_AGENT_MODEL`: model id (for example your Venice test model)
- `LEAD_AGENT_MODEL_PROVIDER`: `openai` or `venice`
- `LEAD_AGENT_MODEL_TIMEOUT_SECONDS`: model request timeout
- `LEAD_AGENT_DOTENV_PATH`: default dotenv path

## Core CLI Commands
Initialize DB:
```bash
python -m lead_agent.cli init-db --db data/leads.db
```

Import sample leads:
```bash
python -m lead_agent.cli import-csv \
  "plan/example-database/OpenClaw Growth OS/Leads 491292f06bc0481ca2e04142cdc0b70c.csv" \
  --db data/leads.db
```

Run discovery once:
```bash
python -m lead_agent.cli run --config config/niches.example.json
```

Run discovery with model:
```bash
python -m lead_agent.cli --dotenv-path .env run --config config/niches.example.json --use-model
```

Requalify unscored leads:
```bash
python -m lead_agent.cli requalify --db data/leads.db --config config/niches.example.json
```

Requalify with model:
```bash
python -m lead_agent.cli --dotenv-path .env requalify --db data/leads.db --config config/niches.example.json --use-model
```

Show aggregate stats:
```bash
python -m lead_agent.cli stats --db data/leads.db
```

## Dashboard UI (Comprehensive)

### Start the UI
Default example:
```bash
PYTHONPATH=src python3 -m lead_agent.cli --dotenv-path .env ui --db data/leads.db --config config/niches.example.json --host 127.0.0.1 --port 1617
```

Open in browser:
- `http://127.0.0.1:1617`

If that port is busy, the app automatically falls back to a free local port and prints the exact URL.

### What You See
- KPI cards:
  - Total leads
  - Hot
  - Warm
  - Cold
  - Unscored
- Filters panel:
  - `Niche`
  - `Band`
  - free-text search (`name`, `website`, `email`, `phone`)
  - row limit (`10` to `500`)
- Actions panel:
  - `Run Discovery`
  - `Requalify Unscored`
  - `Use model` checkbox
- Leads table:
  - name, niche, area, website, email, phone, score, band, website quality, updated time
- Recent Runs table:
  - niche/query with discovered/processed/inserted/updated/errors for each run

### How Actions Work
- `Run Discovery`:
  - Executes the same pipeline as `lead_agent.cli run`
  - Uses your config queries and writes results into SQLite
- `Requalify Unscored`:
  - Executes the same logic as `lead_agent.cli requalify`
  - Attempts to score leads currently without a band
- `Use model` checked:
  - Enables model scoring for that action
  - If model fails or key is missing, heuristic fallback applies

The UI is synchronous for actions: button click waits for completion and then shows command output at top of page.

### Recommended Daily Workflow
1. Open UI and scan KPI cards.
2. Click `Run Discovery` (with `Use model` if desired).
3. Filter by `Unscored` and run `Requalify Unscored`.
4. Filter by `Hot`/`Warm` and manually review websites.
5. Use `Recent Runs` to identify low-quality queries and tune `config/niches.example.json`.

### Safety Characteristics
- Read/write only workflow
- No delete endpoints in UI
- Actions call local CLI commands only
- Data stays in local SQLite file (`data/leads.db`)

## UI Troubleshooting

### `OSError: [Errno 48] Address already in use`
- Cause: another process is already bound to the requested port.
- Current behavior: UI now auto-selects a free port and prints it.
- Optional manual cleanup:
```bash
lsof -i :1617
kill <PID>
```

### UI starts but page is not reachable
- Confirm command uses `--host 127.0.0.1`.
- Check printed URL and open that exact address.
- Ensure no VPN/firewall rule blocks localhost binding.

### Action returns model/key errors
- Check `.env` values.
- Ensure CLI was started with `--dotenv-path .env`.
- For Venice testing, verify `LEAD_AGENT_MODEL_PROVIDER=venice` and matching key/model.

### Requalify shows expected fetch errors
- Some sites are down/blocked/non-HTML.
- This is normal and leads may remain `Unscored` until manual review.

## Config (`config/niches.example.json`)
Edit per niche:
- `niches[].name`
- `niches[].queries`
- `niches[].keywords`
- `niches[].locations`
- `niches[].excluded_domains`

## SQLite Tables
- `leads`: current state
- `lead_sources`: source trace events
- `lead_qualifications`: qualification history snapshots
- `scrape_runs`: run telemetry

## Notes
- Search discovery uses DuckDuckGo HTML endpoint.
- If model is enabled but unavailable, the app falls back to heuristic qualification.
