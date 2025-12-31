# core.py
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.utility import html_escape, render_template_file, json_pretty

ALLOWED_SECTIONS = {"summary", "experience", "skills", "projects", "other"}


# -------------------------
# Pydantic models (domain)
# -------------------------
class MatchItem(BaseModel):
    requirement: str
    status: Literal["match", "partial", "missing"]
    evidence: list[str] = Field(default_factory=list)


class GapItem(BaseModel):
    gap: str
    impact: Literal["high", "medium", "low"]
    how_to_fix: list[str] = Field(default_factory=list)


class SuggestionItem(BaseModel):
    section: str
    change: str
    reason: str
    priority: Literal["high", "medium", "low"] = "medium"

    @field_validator("section", mode="before")
    @classmethod
    def normalize_section(cls, v):
        if not isinstance(v, str):
            return "other"
        s = v.strip().lower()

        mapping = {
            "education": "other",
            "certifications": "skills",
            "certification": "skills",
            "training": "skills",
            "courses": "skills",
            "course": "skills",
            "projects": "projects",
            "project": "projects",
            "work experience": "experience",
            "experience": "experience",
            "skills": "skills",
            "summary": "summary",
            "about": "summary",
        }
        s = mapping.get(s, s)
        return s if s in ALLOWED_SECTIONS else "other"


class LinkedInSuggestionItem(BaseModel):
    section: str = "other"
    change: str
    reason: str
    priority: Literal["high", "medium", "low"] = "medium"


class ATSKeywordItem(BaseModel):
    keyword: str
    where_to_add: Literal["cv", "linkedin", "both"] = "cv"
    note: str = ""


class FitReport(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    summary: str = ""
    must_have_match: list[MatchItem] = Field(default_factory=list)
    nice_to_have_match: list[MatchItem] = Field(default_factory=list)
    gaps: list[GapItem] = Field(default_factory=list)

    cv_suggestions: list[SuggestionItem] = Field(default_factory=list)
    linkedin_suggestions: list[LinkedInSuggestionItem] = Field(default_factory=list)
    ats_keywords: list[ATSKeywordItem] = Field(default_factory=list)
    final_note: str = ""


# -------------------------
# Decision logic (UI)
# -------------------------
CRITICAL_KEYWORDS = {
    "kubernetes",
    "kubeflow",
    "mlflow",
    "dvc",
    "monitoring",
    "observability",
    "versioning",
    "pipeline",
    "ci/cd",
}

def decide_ui(report: FitReport) -> dict:
    fit_score = int(report.fit_score)
    must = report.must_have_match or []
    gaps = report.gaps or []

    missing_must = [m for m in must if m.status == "missing"]
    has_missing_must = len(missing_must) > 0
    has_high_gap = any(g.impact == "high" for g in gaps)

    # Non far ribaltare un punteggio alto: i high gaps al massimo portano a MAYBE.
    if fit_score >= 75:
        if has_missing_must or has_high_gap:
            return dict(
                code="MAYBE",
                badge="maybe",
                label="⚠️ Worth applying only if highly motivated",
                reason="Strong score, but there are gaps that could affect screening. Clarify them with concrete evidence or projects.",
                next_step="Apply only if you can clearly demonstrate or mitigate the highlighted gaps (examples, portfolio, interview framing).",
            )
        return dict(
            code="YES",
            badge="yes",
            label="✅ Worth applying",
            reason="Strong alignment with key requirements; remaining gaps are not blocking.",
            next_step="Apply and tailor your CV and LinkedIn profile to highlight your strengths.",
        )

    # Fascia intermedia
    if fit_score >= 55:
        return dict(
            code="MAYBE",
            badge="maybe",
            label="⚠️ Worth applying only if highly motivated",
            reason="Decent alignment, but concrete evidence is needed to pass initial screening.",
            next_step="Apply only if you can clearly demonstrate the missing skills with real examples or projects.",
        )

    # Fascia bassa
    reason = "Fit currently low."
    if has_missing_must:
        reason = "Some key requirements appear missing, with a high risk of early screening rejection."
    elif has_high_gap:
        reason = "There are high-impact gaps that likely block the role for now."

    return dict(
        code="NO",
        badge="no",
        label="❌ Not worth applying (for now)",
        reason=reason,
        next_step="Focus on better-aligned roles or build targeted projects before applying.",
    )




def _card(text: str, kind: str) -> str:
    icon = "✓" if kind == "pos" else "!"
    return (
        f'<div class="qitem">'
        f'  <div class="qicon {kind}">{icon}</div>'
        f'  <div class="qtext">{html_escape(text)}</div>'
        f"</div>"
    )


# -------------------------
# HTML report rendering
# -------------------------
def render_report_html(report: FitReport, json_path: str, template_path: str, job_title: str = "") -> str:
    d = decide_ui(report)

    must = report.must_have_match or []
    nice = report.nice_to_have_match or []

    strengths = (
        [m.requirement for m in must + nice if m.status == "match"] +
        [m.requirement for m in must + nice if m.status == "partial"]
    )[:3]

    gaps_sorted = sorted(
        report.gaps or [],
        key=lambda g: {"high": 0, "medium": 1, "low": 2}.get(g.impact, 3)
    )
    blockers = [g.gap for g in gaps_sorted if g.impact in ("high", "medium")][:3]

    if not blockers:
        missing_must = [m.requirement for m in must if m.status == "missing"][:3]
        blockers = missing_must


    strengths_cards = "\n".join(_card(x, "pos") for x in strengths[:3]) or '<div class="qempty">Nessun punto a favore rilevante.</div>'
    blockers_cards = "\n".join(_card(x, "neg") for x in blockers[:3]) or '<div class="qempty">Nessun blocco principale.</div>'

    score = max(0, min(100, int(report.fit_score)))
    if score <= 50:
        score_bar_class = "bar-red"
    elif score <= 75:
        score_bar_class = "bar-yellow"
    else:
        score_bar_class = "bar-green"

    values = {
        "json_file_name": html_escape(Path(json_path).name),
        "decision_label": html_escape(d["label"]),
        "decision_reason": html_escape(d["reason"]),
        "decision_badge": html_escape(d["badge"]),
        "decision_code": html_escape(d["code"]),
        "fit_score": html_escape(str(score)),
        "confidence": html_escape(str(report.confidence)),
        "next_step": html_escape(d["next_step"]),
        "summary": html_escape(report.summary),
        "final_note": html_escape(report.final_note),
        "json_dump": html_escape(json_pretty(report.model_dump())),
        "job_title": html_escape(job_title or "Posizione LinkedIn"),
        "score_bar_class": score_bar_class,
        "job_id": html_escape(Path(json_path).parent.name),
        "strengths_cards": strengths_cards,
        "blockers_cards": blockers_cards,
        "strengths_count": html_escape(f"{min(len(strengths),3)}/3"),
        "blockers_count": html_escape(f"{min(len(blockers),3)}/3"),
    }

    return render_template_file(template_path, values)
