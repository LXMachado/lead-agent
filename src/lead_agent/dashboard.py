from __future__ import annotations

import argparse
import errno
import html
import sqlite3
import subprocess
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_stats(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN qualification_band = 'Hot' THEN 1 ELSE 0 END) AS hot,
          SUM(CASE WHEN qualification_band = 'Warm' THEN 1 ELSE 0 END) AS warm,
          SUM(CASE WHEN qualification_band = 'Cold' THEN 1 ELSE 0 END) AS cold,
          SUM(CASE WHEN qualification_band IS NULL THEN 1 ELSE 0 END) AS unscored
        FROM leads
        """
    ).fetchone()
    if not row:
        return {"total": 0, "hot": 0, "warm": 0, "cold": 0, "unscored": 0}
    return {k: int(row[k] or 0) for k in row.keys()}


def _fetch_niches(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT niche FROM leads WHERE niche IS NOT NULL AND TRIM(niche) <> '' ORDER BY niche"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _fetch_recent_runs(conn: sqlite3.Connection, limit: int = 12) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT niche, query, discovered_urls, processed_urls, inserted_leads, updated_leads, errors,
               started_at, finished_at
        FROM scrape_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _fetch_leads(
    conn: sqlite3.Connection,
    niche: str,
    band: str,
    search: str,
    limit: int,
) -> list[sqlite3.Row]:
    clauses = ["1=1"]
    params: list[Any] = []

    if niche:
        clauses.append("niche = ?")
        params.append(niche)

    if band == "Unscored":
        clauses.append("qualification_band IS NULL")
    elif band:
        clauses.append("qualification_band = ?")
        params.append(band)

    if search:
        clauses.append("(name LIKE ? OR website LIKE ? OR email LIKE ? OR phone LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])

    sql = f"""
        SELECT id, name, niche, area, website, email, phone,
               qualification_score, qualification_band, website_quality,
               updated_at
        FROM leads
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
    """
    params.append(limit)
    return conn.execute(sql, tuple(params)).fetchall()


def _badge_class(band: str | None) -> str:
    if band == "Hot":
        return "b-hot"
    if band == "Warm":
        return "b-warm"
    if band == "Cold":
        return "b-cold"
    return "b-unscored"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _run_action(command: list[str], timeout_seconds: int = 7200) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        return False, f"Action failed to execute: {exc}"

    output = (completed.stdout or "").strip()
    if completed.returncode == 0:
        return True, output or "Action finished successfully."
    return False, output or f"Action failed with exit code {completed.returncode}."


def _lead_detail(selected: sqlite3.Row | None) -> str:
    if not selected:
        return (
            '<div class="detail-empty">Select a lead from the table to view details.</div>'
        )

    band = selected["qualification_band"] or "Unscored"
    website = selected["website"] or ""
    website_html = (
        f'<a href="{_esc(website)}" target="_blank" rel="noreferrer">{_esc(website)}</a>'
        if website
        else "-"
    )

    return (
        '<div class="detail-grid">'
        f'<div class="dk">Name</div><div>{_esc(selected["name"]) or "-"}</div>'
        f'<div class="dk">Niche</div><div>{_esc(selected["niche"]) or "-"}</div>'
        f'<div class="dk">Area</div><div>{_esc(selected["area"]) or "-"}</div>'
        f'<div class="dk">Website</div><div>{website_html}</div>'
        f'<div class="dk">Email</div><div>{_esc(selected["email"]) or "-"}</div>'
        f'<div class="dk">Phone</div><div>{_esc(selected["phone"]) or "-"}</div>'
        f'<div class="dk">Score</div><div>{_esc(selected["qualification_score"]) or "-"}</div>'
        f'<div class="dk">Band</div><div><span class="badge {_badge_class(selected["qualification_band"])}">{_esc(band)}</span></div>'
        f'<div class="dk">Quality</div><div>{_esc(selected["website_quality"]) or "-"}</div>'
        f'<div class="dk">Updated</div><div>{_esc(selected["updated_at"]) or "-"}</div>'
        "</div>"
    )


def _build_query(*, niche: str, band: str, search: str, limit: int, selected_id: int | None) -> str:
    params: dict[str, str] = {}
    if niche:
        params["niche"] = niche
    if band:
        params["band"] = band
    if search:
        params["q"] = search
    params["limit"] = str(limit)
    if selected_id is not None:
        params["lead"] = str(selected_id)
    return urllib.parse.urlencode(params)


def _render_html(
    *,
    db_path: str,
    config_path: str,
    dotenv_path: str,
    niche: str,
    band: str,
    search: str,
    limit: int,
    selected_id: int | None,
    message: str,
    message_ok: bool,
) -> str:
    conn = _connect(db_path)
    stats = _fetch_stats(conn)
    niches = _fetch_niches(conn)
    rows = _fetch_leads(conn, niche=niche, band=band, search=search, limit=limit)
    runs = _fetch_recent_runs(conn)

    selected: sqlite3.Row | None = None
    if rows:
        if selected_id is not None:
            for row in rows:
                if int(row["id"]) == selected_id:
                    selected = row
                    break
        if selected is None:
            selected = rows[0]

    query_base = _build_query(
        niche=niche,
        band=band,
        search=search,
        limit=limit,
        selected_id=int(selected["id"]) if selected else None,
    )

    options_niche = ['<option value="">All Niches</option>']
    for n in niches:
        selected_opt = " selected" if n == niche else ""
        options_niche.append(f"<option value=\"{_esc(n)}\"{selected_opt}>{_esc(n)}</option>")

    bands = ["", "Hot", "Warm", "Cold", "Unscored"]
    options_band = []
    for b in bands:
        label = "All Bands" if not b else b
        selected_opt = " selected" if b == band else ""
        options_band.append(f"<option value=\"{_esc(b)}\"{selected_opt}>{_esc(label)}</option>")

    lead_rows = []
    for r in rows:
        band_val = r["qualification_band"]
        band_display = band_val if band_val else "Unscored"
        rid = int(r["id"])
        sel = " selected-row" if selected and rid == int(selected["id"]) else ""
        q = _build_query(niche=niche, band=band, search=search, limit=limit, selected_id=rid)
        lead_rows.append(
            f'<tr class="{sel.strip()}">'
            f'<td><a href="/?{_esc(q)}">{_esc(r["name"])}</a></td>'
            f"<td>{_esc(r['niche'])}</td>"
            f"<td>{_esc(r['area'])}</td>"
            f'<td><a href="{_esc(r["website"])}" target="_blank" rel="noreferrer">{_esc(r["website"])}</a></td>'
            f"<td>{_esc(r['email'])}</td>"
            f"<td>{_esc(r['phone'])}</td>"
            f"<td>{_esc(r['qualification_score'])}</td>"
            f"<td><span class=\"badge {_badge_class(band_val)}\">{_esc(band_display)}</span></td>"
            f"<td>{_esc(r['website_quality'])}</td>"
            f"<td>{_esc(r['updated_at'])}</td>"
            "</tr>"
        )

    run_rows = []
    for rr in runs:
        run_rows.append(
            "<tr>"
            f"<td>{_esc(rr['started_at'])}</td>"
            f"<td>{_esc(rr['niche'])}</td>"
            f"<td>{_esc(rr['query'])}</td>"
            f"<td>{_esc(rr['discovered_urls'])}</td>"
            f"<td>{_esc(rr['processed_urls'])}</td>"
            f"<td>{_esc(rr['inserted_leads'])}</td>"
            f"<td>{_esc(rr['updated_leads'])}</td>"
            f"<td>{_esc(rr['errors'])}</td>"
            "</tr>"
        )

    message_block = ""
    if message:
        status_class = "msg-ok" if message_ok else "msg-err"
        message_block = f"<div class=\"msg {status_class}\"><pre>{_esc(message)}</pre></div>"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>Lead Agent Dashboard</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --surface: #ffffff;
      --panel: #eef4fa;
      --text: #152238;
      --muted: #66758a;
      --line: #d6e1ee;
      --brand: #1f6fff;
      --brand-2: #0f58dd;
      --shadow: 0 8px 24px rgba(19, 35, 72, 0.06);
      --radius: 14px;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: radial-gradient(circle at top left, #f2f6fb 0%, #f8fbfe 55%, #f3f7fc 100%); color: var(--text); font-family: "Manrope", "Avenir Next", "Segoe UI", sans-serif; }}
    .wrap {{ max-width: 1480px; margin: 0 auto; padding: 18px; }}
    .layout {{ display: grid; grid-template-columns: 230px 1fr 460px; gap: 18px; align-items: start; }}
    .intro, .hero, .aside, .section, .cta {{ background: var(--panel); border: 1px solid #e5edf7; border-radius: 18px; }}
    .intro {{ padding: 24px 20px; min-height: 480px; }}
    .logo {{ width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center; background: #fff; border: 1px solid var(--line); font-size: 22px; }}
    .intro h2 {{ margin: 16px 0 12px; font-size: 40px; line-height: 1.05; letter-spacing: -.4px; }}
    .intro p {{ margin: 0; font-size: 16px; line-height: 1.45; color: #2a3950; max-width: 190px; }}

    .hero {{ padding: 12px; }}
    .hero-head {{ background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 12px; box-shadow: var(--shadow); }}
    .topline {{ display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; flex-wrap: wrap; }}
    h1 {{ margin: 0; font-size: 36px; letter-spacing: -.45px; line-height: 1.08; }}
    .tiny {{ color: var(--muted); font-size: 12px; }}
    .cards {{ display: grid; grid-template-columns: repeat(5, minmax(90px, 1fr)); gap: 8px; margin-top: 12px; }}
    .card {{ background: #f8fbff; border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; }}
    .card .k {{ font-size: 30px; line-height: 1; font-weight: 800; letter-spacing: -.2px; }}
    .card .l {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; margin-top: 3px; }}

    .msg {{ border-radius: 10px; padding: 10px; margin-top: 10px; border: 1px solid var(--line); background: #fff; }}
    .msg-ok {{ border-color: #b7e4c7; background: #f1fff5; }}
    .msg-err {{ border-color: #f5c2c7; background: #fff5f6; }}
    pre {{ margin: 0; white-space: pre-wrap; font-size: 12px; }}

    .search-row {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; margin-top: 10px; align-items: stretch; }}
    .panel {{ background: #fff; border: 1px solid var(--line); border-radius: var(--radius); padding: 10px; box-shadow: var(--shadow); }}
    .filters, .actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    input, select, button {{ border-radius: 9px; border: 1px solid var(--line); padding: 8px 10px; font-size: 12px; background: #fff; color: var(--text); }}
    button {{ cursor: pointer; }}
    button.primary {{ background: linear-gradient(180deg, var(--brand) 0%, var(--brand-2) 100%); color: #fff; border-color: #2d70e8; font-weight: 700; }}
    button.secondary {{ background: #fff; }}

    .table-panel {{ background: #fff; border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); margin-top: 10px; overflow: hidden; }}
    .panel-title {{ padding: 10px 12px; border-bottom: 1px solid #edf2f8; color: var(--muted); font-size: 12px; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #edf2f8; text-align: left; padding: 7px 8px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }}
    a {{ color: #1159ce; text-decoration: none; }}
    tr.selected-row {{ background: #f0f6ff; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
    .b-hot {{ background: #d1f2df; color: #0f5132; }}
    .b-warm {{ background: #ffe5c2; color: #7a4100; }}
    .b-cold {{ background: #dbeafe; color: #1d4ed8; }}
    .b-unscored {{ background: #e9ecef; color: #495057; }}

    .aside {{ padding: 30px 28px; min-height: 480px; }}
    .aside h3 {{ margin: 2px 0 12px; font-size: 40px; line-height: 1.08; letter-spacing: -.4px; max-width: 340px; }}
    .aside p {{ margin: 0; color: #2a3950; font-size: 16px; line-height: 1.48; max-width: 330px; }}

    .section-grid {{ margin-top: 18px; display: grid; grid-template-columns: 1.3fr 1fr; gap: 18px; }}
    .section {{ padding: 16px; }}
    .section h4 {{ margin: 6px 0 8px; font-size: 34px; line-height: 1.1; letter-spacing: -.35px; }}
    .section p {{ margin: 0 0 10px; font-size: 16px; line-height: 1.45; color: #2a3950; max-width: 360px; }}
    .detail-card {{ background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px; box-shadow: var(--shadow); }}
    .detail-grid {{ display: grid; grid-template-columns: 110px 1fr; gap: 8px 10px; font-size: 13px; }}
    .dk {{ font-weight: 700; color: #516179; text-transform: uppercase; letter-spacing: .3px; font-size: 11px; }}
    .detail-empty {{ font-size: 13px; color: var(--muted); }}

    .cta {{ margin-top: 18px; padding: 30px 28px; display: flex; align-items: center; justify-content: center; gap: 30px; flex-wrap: wrap; text-align: center; }}
    .cta h5 {{ margin: 0 0 6px; font-size: 38px; line-height: 1.1; letter-spacing: -.3px; }}
    .cta p {{ margin: 0; color: #2a3950; font-size: 15px; line-height: 1.45; }}
    .cta .run {{ padding: 14px 22px; border-radius: 12px; font-size: 30px; min-width: 220px; }}

    @media (max-width: 1360px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .intro, .aside {{ min-height: 0; }}
      .section-grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
      .intro h2, .aside h3, .section h4, .cta h5 {{ font-size: 30px; }}
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"layout\">
      <aside class=\"intro\">
        <div class=\"logo\">🎯</div>
        <h2>Lead Agent Dashboard</h2>
        <p>Monitor discovered leads, spot what is qualified, and keep your outreach pipeline active with structured workflows.</p>
      </aside>

      <section class=\"hero\">
        <div class=\"hero-head\">
          <div class=\"topline\">
            <div>
              <h1>Lead Agent Dashboard</h1>
              <div class=\"tiny\">{_esc(db_path)} · refreshed {now}</div>
            </div>
            <div class=\"tiny\">No delete actions in UI.</div>
          </div>

          <div class=\"cards\">
            <div class=\"card\"><div class=\"k\">{stats['total']}</div><div class=\"l\">Total</div></div>
            <div class=\"card\"><div class=\"k\">{stats['hot']}</div><div class=\"l\">Hot</div></div>
            <div class=\"card\"><div class=\"k\">{stats['warm']}</div><div class=\"l\">Warm</div></div>
            <div class=\"card\"><div class=\"k\">{stats['cold']}</div><div class=\"l\">Cold</div></div>
            <div class=\"card\"><div class=\"k\">{stats['unscored']}</div><div class=\"l\">Unscored</div></div>
          </div>

          {message_block}

          <div class=\"search-row\">
            <div class=\"panel\">
              <form method=\"get\" class=\"filters\">
                <select name=\"niche\">{''.join(options_niche)}</select>
                <select name=\"band\">{''.join(options_band)}</select>
                <input name=\"q\" value=\"{_esc(search)}\" placeholder=\"Search name, website, email, phone\" />
                <input name=\"limit\" type=\"number\" min=\"10\" max=\"500\" value=\"{limit}\" />
                <button class=\"secondary\" type=\"submit\">Filter</button>
              </form>
            </div>
            <div class=\"panel\">
              <form method=\"post\" action=\"/action\" class=\"actions\">
                <input type=\"hidden\" name=\"config\" value=\"{_esc(config_path)}\" />
                <input type=\"hidden\" name=\"db\" value=\"{_esc(db_path)}\" />
                <input type=\"hidden\" name=\"dotenv\" value=\"{_esc(dotenv_path)}\" />
                <input type=\"hidden\" name=\"return_q\" value=\"{_esc(query_base)}\" />
                <label><input type=\"checkbox\" name=\"use_model\" value=\"1\" /> Use model</label>
                <button class=\"primary\" type=\"submit\" name=\"kind\" value=\"run\">Run Discovery</button>
                <button class=\"secondary\" type=\"submit\" name=\"kind\" value=\"requalify\">Requalify Unscored</button>
              </form>
            </div>
          </div>

          <div class=\"table-panel\">
            <div class=\"panel-title\">Leads ({len(rows)} shown)</div>
            <div class=\"table-wrap\">
              <table>
                <thead>
                  <tr>
                    <th>Name</th><th>Niche</th><th>Area</th><th>Website</th><th>Email</th><th>Phone</th>
                    <th>Score</th><th>Band</th><th>Quality</th><th>Updated</th>
                  </tr>
                </thead>
                <tbody>{''.join(lead_rows)}</tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <aside class=\"aside\">
        <h3>Smart filtering and scoring</h3>
        <p>Filter by niche, band, or search term. Run scoring workflows to prioritize follow-up with clear lead bands.</p>
      </aside>
    </div>

    <div class=\"section-grid\">
      <section class=\"section\">
        <h4>Recent runs</h4>
        <p>Track discovery execution with detailed metrics and error counts.</p>
        <div class=\"detail-card\">
          <div class=\"table-wrap\">
            <table>
              <thead>
                <tr>
                  <th>Started</th><th>Niche</th><th>Query</th><th>Discovered</th><th>Processed</th><th>Inserted</th><th>Updated</th><th>Errors</th>
                </tr>
              </thead>
              <tbody>{''.join(run_rows)}</tbody>
            </table>
          </div>
        </div>
      </section>

      <section class=\"section\">
        <h4>Lead details</h4>
        <p>Quickly review lead signals, score, and latest update timestamp.</p>
        <div class=\"detail-card\">
          {_lead_detail(selected)}
        </div>
      </section>
    </div>

    <div class=\"cta\">
      <div>
        <h5>Automated discovery</h5>
        <p>Fetch new leads from multiple niches and keep your pipeline current with one click.</p>
      </div>
      <form method=\"post\" action=\"/action\" class=\"actions\">
        <input type=\"hidden\" name=\"config\" value=\"{_esc(config_path)}\" />
        <input type=\"hidden\" name=\"db\" value=\"{_esc(db_path)}\" />
        <input type=\"hidden\" name=\"dotenv\" value=\"{_esc(dotenv_path)}\" />
        <input type=\"hidden\" name=\"return_q\" value=\"{_esc(query_base)}\" />
        <button class=\"primary run\" type=\"submit\" name=\"kind\" value=\"run\">Run Discovery</button>
      </form>
    </div>
  </div>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    db_path: str = "data/leads.db"
    config_path: str = "config/niches.example.json"
    dotenv_path: str = ".env"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        niche = (qs.get("niche", [""])[0] or "").strip()
        band = (qs.get("band", [""])[0] or "").strip()
        search = (qs.get("q", [""])[0] or "").strip()

        limit = 100
        try:
            limit = max(10, min(int(qs.get("limit", ["100"])[0]), 500))
        except Exception:
            limit = 100

        selected_id: int | None = None
        try:
            selected_id = int((qs.get("lead", [""])[0] or "").strip())
        except Exception:
            selected_id = None

        msg = (qs.get("msg", [""])[0] or "").strip()
        ok = (qs.get("ok", ["1"])[0] or "1") == "1"

        body = _render_html(
            db_path=self.db_path,
            config_path=self.config_path,
            dotenv_path=self.dotenv_path,
            niche=niche,
            band=band,
            search=search,
            limit=limit,
            selected_id=selected_id,
            message=msg,
            message_ok=ok,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/action":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        form = urllib.parse.parse_qs(raw)

        kind = (form.get("kind", [""])[0] or "").strip()
        db = (form.get("db", [self.db_path])[0] or self.db_path).strip()
        cfg = (form.get("config", [self.config_path])[0] or self.config_path).strip()
        dotenv = (form.get("dotenv", [self.dotenv_path])[0] or self.dotenv_path).strip()
        use_model = "use_model" in form
        return_q = (form.get("return_q", [""])[0] or "").strip()

        cmd = [sys.executable, "-m", "lead_agent.cli", "--dotenv-path", dotenv]

        if kind == "run":
            cmd.extend(["run", "--config", cfg])
            if use_model:
                cmd.append("--use-model")
        elif kind == "requalify":
            cmd.extend(["requalify", "--db", db, "--config", cfg])
            if use_model:
                cmd.append("--use-model")
        else:
            self._redirect("Unknown action.", ok=False, return_q=return_q)
            return

        ok, output = _run_action(cmd)
        compact = output[:4000]
        self._redirect(compact, ok=ok, return_q=return_q)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _redirect(self, message: str, ok: bool, return_q: str = "") -> None:
        params: dict[str, str] = {"msg": message, "ok": "1" if ok else "0"}
        if return_q:
            for key, vals in urllib.parse.parse_qs(return_q).items():
                if vals:
                    params[key] = vals[0]
        q = urllib.parse.urlencode(params)
        self.send_response(303)
        self.send_header("Location", f"/?{q}")
        self.end_headers()


def run_dashboard(db_path: str, config_path: str, dotenv_path: str, host: str, port: int) -> None:
    DashboardHandler.db_path = db_path
    DashboardHandler.config_path = config_path
    DashboardHandler.dotenv_path = dotenv_path

    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # Fall back to any available local port if the requested one is already in use.
        server = ThreadingHTTPServer((host, 0), DashboardHandler)
        chosen_port = int(server.server_address[1])
        print(
            f"Port {port} is already in use. "
            f"Started dashboard on http://{host}:{chosen_port} instead."
        )
    else:
        chosen_port = port
        print(f"Dashboard running at http://{host}:{chosen_port}")

    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Lead Agent dashboard")
    parser.add_argument("--db", default="data/leads.db")
    parser.add_argument("--config", default="config/niches.example.json")
    parser.add_argument("--dotenv-path", default=".env")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    run_dashboard(args.db, args.config, args.dotenv_path, args.host, args.port)


if __name__ == "__main__":
    main()
