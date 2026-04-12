from __future__ import annotations

from dataclasses import dataclass

from .config import NicheConfig


@dataclass(slots=True)
class Qualification:
    score: int
    band: str
    website_quality: str
    website_notes: str
    reasons: list[str]


def _derive_quality(word_count: int, has_email: bool, has_phone: bool, has_contact_page_hint: bool) -> tuple[str, str]:
    if word_count < 120:
        return "Broken", "Very low page content or inaccessible content."

    score = 0
    if word_count >= 350:
        score += 2
    if has_contact_page_hint:
        score += 1
    if has_email:
        score += 1
    if has_phone:
        score += 1

    if score >= 5:
        return "Excellent", "Well-structured site with strong contact signals."
    if score >= 3:
        return "Strong", "Good baseline site and clear service/contact signals."
    if score == 2:
        return "Opportunity", "Basic site footprint with moderate room for improvement."
    return "Weak", "Limited digital quality signals; likely conversion improvement opportunity."


def _fit_band(score: int) -> str:
    if score >= 80:
        return "Hot"
    if score >= 55:
        return "Warm"
    return "Cold"


def qualify(
    niche: NicheConfig,
    page_text: str,
    word_count: int,
    has_email: bool,
    has_phone: bool,
    inferred_area: str | None,
) -> Qualification:
    text_lower = page_text.lower()
    reasons: list[str] = []

    website_quality, website_notes = _derive_quality(
        word_count=word_count,
        has_email=has_email,
        has_phone=has_phone,
        has_contact_page_hint=("contact" in text_lower),
    )

    score = 30

    if has_email:
        score += 15
        reasons.append("Email detected")
    else:
        reasons.append("No email detected")

    if has_phone:
        score += 10
        reasons.append("Phone detected")
    else:
        reasons.append("No phone detected")

    keyword_hits = sum(1 for kw in niche.keywords if kw and kw in text_lower)
    if keyword_hits:
        score += min(keyword_hits * 8, 24)
        reasons.append(f"Niche keyword matches: {keyword_hits}")
    else:
        reasons.append("No niche keyword matches")

    if inferred_area and any(loc in inferred_area.lower() for loc in niche.locations):
        score += 10
        reasons.append("Area aligned to niche location")

    quality_bonus = {
        "Excellent": 20,
        "Strong": 12,
        "Opportunity": 6,
        "Weak": 3,
        "Broken": 0,
    }
    score += quality_bonus[website_quality]
    reasons.append(f"Website quality: {website_quality}")

    score = max(0, min(score, 100))
    band = _fit_band(score)

    return Qualification(
        score=score,
        band=band,
        website_quality=website_quality,
        website_notes=website_notes,
        reasons=reasons,
    )
