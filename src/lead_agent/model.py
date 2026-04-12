from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.request import Request, urlopen

from .config import NicheConfig
from .qualify import Qualification

JSON_OBJ_REGEX = re.compile(r"\{[\s\S]*\}")


class ModelQualificationError(Exception):
    pass


@dataclass(slots=True)
class ModelInput:
    niche: NicheConfig
    business_name: str
    website: str
    area: str | None
    word_count: int
    title: str | None
    description: str | None
    has_email: bool
    has_phone: bool
    text_excerpt: str


def _normalize_quality(value: str) -> str:
    allowed = {"Broken", "Weak", "Opportunity", "Strong", "Excellent"}
    for item in allowed:
        if value.strip().lower() == item.lower():
            return item
    return "Opportunity"


def _normalize_band(value: str) -> str:
    allowed = {"Cold", "Warm", "Hot"}
    for item in allowed:
        if value.strip().lower() == item.lower():
            return item
    return "Cold"


def _coerce_qualification(payload: dict[str, object]) -> Qualification:
    score_raw = payload.get("score", 50)
    try:
        score = int(score_raw)
    except Exception:
        score = 50
    score = max(0, min(100, score))

    band = _normalize_band(str(payload.get("band", "Cold")))
    website_quality = _normalize_quality(str(payload.get("website_quality", "Opportunity")))
    website_notes = str(payload.get("website_notes", "Model generated qualification.")).strip()
    if not website_notes:
        website_notes = "Model generated qualification."

    reasons = _safe_str_list(payload.get("reasons"))
    if not reasons:
        reasons = ["Model generated qualification."]

    return Qualification(
        score=score,
        band=band,
        website_quality=website_quality,
        website_notes=website_notes,
        reasons=reasons,
    )


def _safe_str_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value:
            out.append(value)
    return out


def _extract_openai_text_output(response_payload: dict[str, object]) -> str:
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = response_payload.get("output")
    if not isinstance(output, list):
        raise ModelQualificationError("missing output from model response")

    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            text = c.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)

    merged = "\n".join(chunks).strip()
    if not merged:
        raise ModelQualificationError("model response had no text content")
    return merged


def _extract_venice_text_output(response_payload: dict[str, object]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelQualificationError("missing choices from Venice response")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ModelQualificationError("invalid Venice choice format")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ModelQualificationError("missing message in Venice choice")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelQualificationError("model response had no text content")
    return content.strip()


def _build_prompt(item: ModelInput) -> dict[str, object]:
    return {
        "task": "Qualify a business lead for outreach",
        "rules": [
            "Return strict JSON only.",
            "score must be 0..100",
            "band must be one of: Cold, Warm, Hot",
            "website_quality must be one of: Broken, Weak, Opportunity, Strong, Excellent",
            "reasons must be a short list of strings",
        ],
        "lead": {
            "business_name": item.business_name,
            "website": item.website,
            "area": item.area,
            "niche_name": item.niche.name,
            "niche_keywords": item.niche.keywords,
            "niche_locations": item.niche.locations,
            "title": item.title,
            "description": item.description,
            "has_email": item.has_email,
            "has_phone": item.has_phone,
            "word_count": item.word_count,
            "text_excerpt": item.text_excerpt[:4000],
        },
        "output_schema": {
            "score": "integer",
            "band": "Cold|Warm|Hot",
            "website_quality": "Broken|Weak|Opportunity|Strong|Excellent",
            "website_notes": "string",
            "reasons": ["string"],
        },
    }


def _call_openai(api_key: str, model: str, prompt: dict[str, object], timeout_seconds: int) -> str:
    endpoint = "https://api.openai.com/v1/responses"
    body = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=True)}],
            }
        ],
    }

    req = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8", errors="ignore")

    payload = json.loads(raw)
    return _extract_openai_text_output(payload)


def _call_openrouter(api_key: str, model: str, prompt: dict[str, object], timeout_seconds: int) -> str:
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=True)}],
    }

    req = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/LXMachado/lead-agent",
            "X-Title": "Lead Agent",
        },
        method="POST",
    )

    with urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8", errors="ignore")

    payload = json.loads(raw)
    return _extract_venice_text_output(payload)


def _call_venice(api_key: str, model: str, prompt: dict[str, object], timeout_seconds: int) -> str:
    endpoint = "https://api.venice.ai/api/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=True)}],
    }

    req = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(req, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8", errors="ignore")

    payload = json.loads(raw)
    return _extract_venice_text_output(payload)


def qualify_with_model(
    *,
    api_key: str,
    model: str,
    provider: str,
    timeout_seconds: int,
    item: ModelInput,
) -> Qualification:
    prompt = _build_prompt(item)

    if provider == "venice":
        text = _call_venice(api_key, model, prompt, timeout_seconds)
    elif provider == "openrouter":
        text = _call_openrouter(api_key, model, prompt, timeout_seconds)
    else:
        text = _call_openai(api_key, model, prompt, timeout_seconds)

    candidate_json = text
    match = JSON_OBJ_REGEX.search(text)
    if match:
        candidate_json = match.group(0)

    try:
        parsed = json.loads(candidate_json)
    except Exception as exc:
        raise ModelQualificationError(f"model output was not valid JSON object: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ModelQualificationError("model output JSON was not an object")

    return _coerce_qualification(parsed)
