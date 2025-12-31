# web_helpers.py
from __future__ import annotations

import os
import json
import re
import uuid
from pathlib import Path
from typing import Optional, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from datapizza.clients.openai import OpenAIClient

from app.core import (
    FitReport,
    MatchItem,
    GapItem,
    SuggestionItem,
    LinkedInSuggestionItem,
    ATSKeywordItem,
    render_report_html,
)
from app.utility import write_text, json_pretty, normalize_spaced_text

load_dotenv()


# -------------------------
# Job paths helpers
# -------------------------
def new_job_id() -> str:
    return uuid.uuid4().hex


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(project_root: Path, job_id: str) -> Path:
    return ensure_dir(project_root / "jobs" / job_id)


def out_dir(project_root: Path, job_id: str) -> Path:
    return ensure_dir(project_root / "outputs" / job_id)


def save_text_input(project_root: Path, job_id: str, filename: str, content: str) -> Path:
    path = job_dir(project_root, job_id) / filename
    write_text(path, content)
    return path


# -------------------------
# LLM client
# -------------------------
def build_client() -> OpenAIClient:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment (.env).")

    # Lasciamo vuoto = default del client (se supportato).
    model = os.getenv("OPENAI_MODEL", "").strip() or None
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

    return OpenAIClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=0.0,
        max_retries=2,
    )


# -------------------------
# JSON extraction / repair
# -------------------------
def extract_json_safely(text: str) -> str:
    """
    Estrae il primo blocco JSON {...} e applica riparazioni MINIME:
    - rimuove CR/LF
    - rimuove trailing commas
    """
    if not text:
        raise ValueError("Empty LLM output")

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError("No JSON object found in LLM output")

    s = m.group(0).strip()
    s = s.replace("\r", " ").replace("\n", " ")
    s = re.sub(r",\s*([}\]])", r"\1", s)  # trailing commas
    return s


# -------------------------
# Two-step response models
# -------------------------
class FitCore(BaseModel):
    fit_score: int = Field(default=0, ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    must_have_match: list[MatchItem] = Field(default_factory=list)
    nice_to_have_match: list[MatchItem] = Field(default_factory=list)
    gaps: list[GapItem] = Field(default_factory=list)


class FitSuggestions(BaseModel):
    summary: str = ""
    cv_suggestions: list[SuggestionItem] = Field(default_factory=list)
    linkedin_suggestions: list[LinkedInSuggestionItem] = Field(default_factory=list)
    ats_keywords: list[ATSKeywordItem] = Field(default_factory=list)
    final_note: str = ""


# -------------------------
# LLM steps
# -------------------------
def analyze_fit_core(
    *,
    client: OpenAIClient,
    system_prompt: str,
    user_template: str,
    cv_text: str,
    job_text: str,
    debug_dir: Optional[Path] = None,
) -> FitCore:
    user_prompt = user_template.format(cv_text=cv_text, job_text=job_text)

    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
        write_text(debug_dir / "fit_core_system.txt", system_prompt)
        write_text(debug_dir / "fit_core_user_prompt.txt", user_prompt)

    resp = client.invoke(
        input=user_prompt,
        system_prompt=system_prompt,
        temperature=0.0,
        max_tokens=3000,
    )

    raw = resp.text
    if debug_dir:
        write_text(debug_dir / "fit_core_raw.txt", raw)

    json_text = extract_json_safely(raw)
    if debug_dir:
        write_text(debug_dir / "fit_core_json_extracted.txt", json_text)

    try:
        data = json.loads(json_text)
    except Exception as e:
        if debug_dir:
            write_text(debug_dir / "fit_core_parse_error.txt", str(e))
        raise

    try:
        return FitCore.model_validate(data)
    except Exception as e:
        if debug_dir:
            write_text(debug_dir / "fit_core_validation_error.txt", str(e))
        raise


def analyze_fit_suggestions(
    *,
    client: OpenAIClient,
    system_prompt: str,
    user_template: str,
    cv_text: str,
    job_text: str,
    fit_core: FitCore,
    debug_dir: Optional[Path] = None,
) -> FitSuggestions:
    user_prompt = user_template.format(
        cv_text=cv_text,
        job_text=job_text,
        fit_core_json=json_pretty(fit_core.model_dump()),
    )

    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
        write_text(debug_dir / "fit_suggestions_system.txt", system_prompt)
        write_text(debug_dir / "fit_suggestions_user_prompt.txt", user_prompt)

    resp = client.invoke(
        input=user_prompt,
        system_prompt=system_prompt,
        temperature=0.0,
        max_tokens=3000,
    )

    raw = resp.text
    if debug_dir:
        write_text(debug_dir / "fit_suggestions_raw.txt", raw)

    json_text = extract_json_safely(raw)
    if debug_dir:
        write_text(debug_dir / "fit_suggestions_json_extracted.txt", json_text)

    try:
        data = json.loads(json_text)
    except Exception as e:
        if debug_dir:
            write_text(debug_dir / "fit_suggestions_parse_error.txt", str(e))
        raise

    try:
        return FitSuggestions.model_validate(data)
    except Exception as e:
        if debug_dir:
            write_text(debug_dir / "fit_suggestions_validation_error.txt", str(e))
        raise

def compute_fit_score(
    must_have: list[MatchItem],
    nice_to_have: list[MatchItem],
    must_total: float = 70.0,
    nice_total: float = 30.0,
) -> int:
    def section(items: list[MatchItem], total: float) -> float:
        if not items:
            return 0.0
        w = total / len(items)
        s = 0.0
        for it in items:
            if it.status == "match":
                s += w
            elif it.status == "partial":
                s += 0.5 * w
        return s

    score = section(must_have, must_total) + section(nice_to_have, nice_total)
    # clamp + round
    score_i = int(round(score))
    return max(0, min(100, score_i))


# -------------------------
# Pipeline
# -------------------------
def process_job(
    *,
    app_root: Path,
    templates_dir: Path,
    job_id: str,
    system_prompt_core: str,
    user_template_core: str,
    system_prompt_sugg: str,
    user_template_sugg: str,
    cv_txt: str,
    job_txt: str,
    job_title: str = "",
) -> dict:
    client = build_client()

    # Debug folder for LLM artifacts
    llm_debug_dir = ensure_dir(job_dir(app_root, job_id) / "llm")

    # Normalize inputs ONCE, before any LLM call
    cv_txt = normalize_spaced_text(cv_txt)
    job_txt = normalize_spaced_text(job_txt)

    # Step 1: core fit (critical)
    fit_core = analyze_fit_core(
        client=client,
        system_prompt=system_prompt_core,
        user_template=user_template_core,
        cv_text=cv_txt,
        job_text=job_txt,
        debug_dir=llm_debug_dir,
    )

    computed_score = compute_fit_score(
        fit_core.must_have_match,
        fit_core.nice_to_have_match,
    )

    # Step 2: suggestions (non critical)
    try:
        suggestions = analyze_fit_suggestions(
            client=client,
            system_prompt=system_prompt_sugg,
            user_template=user_template_sugg,
            cv_text=cv_txt,
            job_text=job_txt,
            fit_core=fit_core,
            debug_dir=llm_debug_dir,
        )
    except Exception as e:
        write_text(llm_debug_dir / "fit_suggestions_error.txt", str(e))
        suggestions = FitSuggestions()  # defaults vuoti

    # Merge into final FitReport
    final_report = FitReport(
        fit_score=computed_score,
        confidence=fit_core.confidence,
        summary=suggestions.summary,
        must_have_match=fit_core.must_have_match,
        nice_to_have_match=fit_core.nice_to_have_match,
        gaps=fit_core.gaps,
        cv_suggestions=suggestions.cv_suggestions,
        linkedin_suggestions=suggestions.linkedin_suggestions,
        ats_keywords=suggestions.ats_keywords,
        final_note=suggestions.final_note,
    )

    # Output
    out = out_dir(app_root, job_id)
    json_path = out / "fit_report.json"
    write_text(json_path, json_pretty(final_report.model_dump()))

    html = render_report_html(
        final_report,
        json_path=str(json_path),
        template_path=str(templates_dir / "report.html"),
        job_title=job_title,
    )
    html_path = out / "report.html"
    write_text(html_path, html)

    return {"json_path": str(json_path), "html_path": str(html_path)}
