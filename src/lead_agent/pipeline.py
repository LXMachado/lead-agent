from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .config import AgentConfig, NicheConfig
from .db import finish_run, start_run, upsert_lead
from .discovery import DiscoveryError, search_urls
from .model import ModelInput, ModelQualificationError, qualify_with_model
from .models import LeadCandidate
from .qualify import qualify
from .scrape import ScrapeError, extract_page_signals, fetch_html


@dataclass(slots=True)
class PipelineStats:
    discovered_urls: int = 0
    processed_urls: int = 0
    inserted: int = 0
    updated: int = 0
    errors: int = 0


def _infer_business_name(title: str | None, fallback_url: str) -> str:
    if title:
        parts = [p.strip() for p in title.replace("|", "-").split("-") if p.strip()]
        if parts:
            return parts[0][:140]
    parsed = urlparse(fallback_url)
    return parsed.netloc.removeprefix("www.")


def _infer_area(niche: NicheConfig, text: str, query: str) -> str | None:
    text_lower = text.lower()
    for loc in niche.locations:
        if loc in text_lower:
            return loc.title()

    query_lower = query.lower()
    for loc in niche.locations:
        if loc in query_lower:
            return loc.title()
    return None


def _excluded(url: str, niche: NicheConfig) -> bool:
    host = urlparse(url).netloc.lower()
    if any(host.endswith(domain) for domain in niche.excluded_domains):
        return True

    blocked = ("facebook.com", "instagram.com", "linkedin.com", "youtube.com", "yelp.")
    return any(site in host for site in blocked)


def run_single_query(conn, config: AgentConfig, niche: NicheConfig, query: str) -> PipelineStats:
    stats = PipelineStats()
    run_id = start_run(conn, niche=niche.name, query=query)

    try:
        urls = search_urls(
            query=query,
            user_agent=config.user_agent,
            timeout_seconds=config.request_timeout_seconds,
            limit=config.max_search_results_per_query,
        )
    except DiscoveryError:
        finish_run(conn, run_id, 0, 0, 0, 0, 1)
        return PipelineStats(errors=1)

    stats.discovered_urls = len(urls)

    for url in urls[: config.max_pages_per_run]:
        if _excluded(url, niche):
            continue

        try:
            html = fetch_html(
                url=url,
                user_agent=config.user_agent,
                timeout_seconds=config.request_timeout_seconds,
            )
            signals = extract_page_signals(html)
        except ScrapeError:
            stats.errors += 1
            continue

        stats.processed_urls += 1

        text = str(signals.get("text") or "")
        title = str(signals.get("title") or "") or None
        email = signals.get("email")
        phone = signals.get("phone")
        area = _infer_area(niche, text=text, query=query)
        word_count = int(signals.get("word_count") or 0)

        q = qualify(
            niche=niche,
            page_text=text,
            word_count=word_count,
            has_email=bool(email),
            has_phone=bool(phone),
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
                        business_name=_infer_business_name(title=title, fallback_url=url),
                        website=url,
                        area=area,
                        word_count=word_count,
                        title=title,
                        description=str(signals.get("description") or "") or None,
                        has_email=bool(email),
                        has_phone=bool(phone),
                        text_excerpt=text,
                    ),
                )
            except ModelQualificationError:
                pass

        candidate = LeadCandidate(
            business_name=_infer_business_name(title=title, fallback_url=url),
            website=url,
            email=str(email) if email else None,
            phone=str(phone) if phone else None,
            area=area,
            niche=niche.name,
            source_url=url,
            source_query=query,
            source_type="scraped",
            website_quality=q.website_quality,
            website_notes=q.website_notes,
            qualification_score=q.score,
            qualification_band=q.band,
            qualification_reasons=q.reasons,
        )

        result = upsert_lead(conn, candidate)
        if result.inserted:
            stats.inserted += 1
        else:
            stats.updated += 1

    finish_run(
        conn,
        run_id,
        discovered_urls=stats.discovered_urls,
        processed_urls=stats.processed_urls,
        inserted=stats.inserted,
        updated=stats.updated,
        errors=stats.errors,
    )
    return stats
