from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class LeadCandidate:
    business_name: str
    website: str
    email: str | None
    phone: str | None
    area: str | None
    niche: str
    source_url: str
    source_query: str
    source_type: str = "scraped"
    notes: str = "Added from web discovery."
    website_quality: str | None = None
    website_notes: str | None = None
    qualification_score: int | None = None
    qualification_band: str | None = None
    qualification_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UpsertResult:
    inserted: bool
    lead_id: int
