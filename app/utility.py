# utility.py
from __future__ import annotations

import io
import re
import json
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


# -------------------------
# File / Text helpers
# -------------------------
def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore").strip()


def write_text(path: str | Path, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content or "", encoding="utf-8")


def sanitize_whitespace(text: str) -> str:
    text = text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def remove_show_more_less(text: str) -> str:
    lines = []
    for line in (l.strip() for l in (text or "").splitlines()):
        if not line:
            continue
        low = line.lower()
        if low in {"show more", "show less"}:
            continue
        lines.append(line)
    return "\n".join(lines)



def clamp_chars(text: str, max_chars: int) -> str:
    text = text or ""
    return text[:max_chars].strip()


# -------------------------
# PDF -> text
# -------------------------
def pdf_bytes_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return sanitize_whitespace("\n".join(parts))


# -------------------------
# HTML Fetch + LinkedIn job extraction (best-effort)
# -------------------------
def fetch_html(url: str, timeout: int = 20) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def looks_like_authwall(html: str) -> bool:
    h = (html or "").lower()

    job_signals = [
        "jobs-guest-frontend",
        "d_jobs_guest_details",
        "mx-details-container-padding",
        "description__text",
        "decorated-job-posting__details",
    ]
    if any(s in h for s in job_signals):
        return False

    strong_authwall = [
        "authwall",
        "checkpoint/challenge",
        "/uas/login",
        "session_redirect",
        "fromsignin=true",
    ]
    return any(s in h for s in strong_authwall)



def extract_job_text_from_linkedin_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    main = soup.select_one("main#main-content") or soup

    details = main.select_one("div.details.mx-details-container-padding")
    if not details:
        details = main.select_one(".details.mx-details-container-padding")
    if not details:
        details = main

    # opzionale: prova a prendere solo la descrizione (più pulito)
    desc = details.select_one(".description__text")
    target = desc or details

    text = target.get_text("\n", strip=True)
    text = remove_show_more_less(text)
    return sanitize_whitespace(text)

def extract_linkedin_job_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.top-card-layout__title") or soup.select_one("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    return sanitize_whitespace(title)




# -------------------------
# HTML template rendering
# -------------------------
def html_escape(s: str) -> str:
    s = s or ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )



def render_template_file(template_path: str | Path, values: dict[str, str]) -> str:
    tpl = Path(template_path).read_text(encoding="utf-8")
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def json_pretty(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def normalize_spaced_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    # Heuristica: se troviamo abbastanza pattern lettera-spazio-lettera
    # allora è quasi certamente testo spaziato.
    pairs = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]\s+[A-Za-zÀ-ÖØ-öø-ÿ0-9]", text)
    if len(pairs) < 25:
        # testo normale: facciamo solo pulizia spazi multipli
        return re.sub(r"\s{2,}", " ", text).strip()

    # Collassa sequenze lettera-spazio-lettera
    text = re.sub(r"([A-Za-zÀ-ÖØ-öø-ÿ0-9])\s+(?=[A-Za-zÀ-ÖØ-öø-ÿ0-9])", r"\1", text)

    # Ripulisci spazi multipli dopo il collasso
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text

