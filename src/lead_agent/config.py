from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class NicheConfig:
    name: str
    queries: list[str]
    keywords: list[str]
    locations: list[str]
    excluded_domains: list[str]


@dataclass(slots=True)
class AgentConfig:
    database_path: Path
    user_agent: str
    request_timeout_seconds: int
    max_search_results_per_query: int
    max_pages_per_run: int
    model_enabled: bool
    model_name: str
    model_api_key: str | None
    model_provider: str
    model_timeout_seconds: int
    niches: list[NicheConfig]


def _normalize_list(raw: Any) -> list[str]:
    if not raw:
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path) -> AgentConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))

    niches: list[NicheConfig] = []
    for raw in data.get("niches", []):
        niche = NicheConfig(
            name=str(raw["name"]).strip(),
            queries=_normalize_list(raw.get("queries")),
            keywords=[x.lower() for x in _normalize_list(raw.get("keywords"))],
            locations=[x.lower() for x in _normalize_list(raw.get("locations"))],
            excluded_domains=[x.lower() for x in _normalize_list(raw.get("excluded_domains"))],
        )
        if not niche.name or not niche.queries:
            continue
        niches.append(niche)

    if not niches:
        raise ValueError("Config must include at least one niche with one query.")

    return AgentConfig(
        database_path=Path(data.get("database_path", "data/leads.db")),
        user_agent=str(data.get("user_agent", "LeadAgent/1.0 (+local)")),
        request_timeout_seconds=int(data.get("request_timeout_seconds", 12)),
        max_search_results_per_query=int(data.get("max_search_results_per_query", 10)),
        max_pages_per_run=int(data.get("max_pages_per_run", 100)),
        model_enabled=_env_bool("LEAD_AGENT_USE_MODEL", default=False),
        model_name=os.getenv("LEAD_AGENT_MODEL", "gpt-5.4-mini"),
        model_api_key=os.getenv("LEAD_AGENT_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY"),
        model_provider=os.getenv("LEAD_AGENT_MODEL_PROVIDER", "openai"),
        model_timeout_seconds=int(os.getenv("LEAD_AGENT_MODEL_TIMEOUT_SECONDS", "30")),
        niches=niches,
    )
