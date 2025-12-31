# webapp.py
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates

from app.utility import (
    pdf_bytes_to_text,
    sanitize_whitespace,
    fetch_html,
    looks_like_authwall,
    extract_job_text_from_linkedin_html,
    extract_linkedin_job_title,
)
from app.web_helpers import (
    new_job_id,
    save_text_input,
    job_dir,
    process_job,
)

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

TEMPLATES_DIR = APP_DIR / "templates"
PROMPTS_DIR = APP_DIR / "prompts"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


app = FastAPI()

# MVP: stato in memoria (poi DB/Redis)
JOB_STATUS: dict[str, dict] = {}


def load_prompt_md(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file missing: {path}")
    return path.read_text(encoding="utf-8").strip()


SYSTEM_PROMPT_CORE = load_prompt_md("hr_fit_core_system.md")
USER_TEMPLATE_CORE = load_prompt_md("hr_fit_core_user.md")

SYSTEM_PROMPT_SUGG = load_prompt_md("hr_fit_suggestions_system.md")
USER_TEMPLATE_SUGG = load_prompt_md("hr_fit_suggestions_user.md")


def set_status(job_id: str, status: str, error: str | None = None, **extra) -> None:
    JOB_STATUS[job_id] = {"status": status, "error": error, **extra}


def run_job(job_id: str, cv_txt: str, job_txt: str, job_title: str) -> None:
    try:
        set_status(job_id, "RUNNING")

        paths = process_job(
            app_root=PROJECT_ROOT,
            templates_dir=TEMPLATES_DIR,
            job_id=job_id,
            system_prompt_core=SYSTEM_PROMPT_CORE,
            user_template_core=USER_TEMPLATE_CORE,
            system_prompt_sugg=SYSTEM_PROMPT_SUGG,
            user_template_sugg=USER_TEMPLATE_SUGG,
            cv_txt=cv_txt,
            job_txt=job_txt,
            job_title=job_title,
        )

        set_status(job_id, "DONE", None, **paths)
    except Exception as e:
        # salva sempre l'errore su disco, così non lo perdi
        try:
            save_text_input(PROJECT_ROOT, job_id, "error.txt", str(e))
        except Exception:
            pass
        set_status(job_id, "ERROR", str(e))


@app.get("/", response_class=HTMLResponse)
def page_upload(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.post("/upload")
def upload(
    background_tasks: BackgroundTasks,
    cv: UploadFile = File(...),
    job_url: str = Form(...),
):
    job_id = new_job_id()
    set_status(job_id, "UPLOADED")

    # ---- CV -> text
    cv_bytes = cv.file.read() or b""
    if not cv_bytes:
        set_status(job_id, "ERROR", "File CV vuoto o non leggibile.")
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)

    cv_name = (cv.filename or "").lower()
    if cv_name.endswith(".pdf"):
        cv_txt = pdf_bytes_to_text(cv_bytes)
    else:
        cv_txt = cv_bytes.decode("utf-8", errors="ignore")

    cv_txt = sanitize_whitespace(cv_txt)
    save_text_input(PROJECT_ROOT, job_id, "cv.txt", cv_txt)

    # ---- Job URL (required)
    job_url_clean = (job_url or "").strip()
    if not job_url_clean:
        set_status(job_id, "ERROR", "URL della posizione LinkedIn mancante.")
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)

    # fetch HTML
    try:
        html = fetch_html(job_url_clean)
    except Exception as e:
        save_text_input(PROJECT_ROOT, job_id, "fetch_error.txt", str(e))
        set_status(job_id, "ERROR", "Errore nel recupero della pagina LinkedIn (fetch).")
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)

    # salva raw html per debug
    save_text_input(PROJECT_ROOT, job_id, "linkedin_job_raw.html", html)

    # authwall check
    if looks_like_authwall(html):
        set_status(job_id, "ERROR", "LinkedIn ha restituito una pagina di login/authwall.")
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)

    # parse job title + job text
    job_title = extract_linkedin_job_title(html)
    job_txt = extract_job_text_from_linkedin_html(html)
    job_txt = sanitize_whitespace(job_txt)

    # ---- LLM background
    background_tasks.add_task(run_job, job_id, cv_txt, job_txt, job_title)
    return RedirectResponse(url=f"/wait/{job_id}", status_code=303)


@app.get("/wait/{job_id}", response_class=HTMLResponse)
def page_wait(request: Request, job_id: str):
    return templates.TemplateResponse("wait.html", {"request": request, "job_id": job_id})


@app.get("/api/status/{job_id}")
def api_status(job_id: str):
    data = JOB_STATUS.get(job_id)
    if not data:
        return JSONResponse({"status": "NOT_FOUND", "error": None}, status_code=404)
    return JSONResponse(data)


@app.get("/report/{job_id}")
def page_report(job_id: str):
    data = JOB_STATUS.get(job_id)
    if not data:
        return RedirectResponse(url="/", status_code=303)
    if data.get("status") != "DONE":
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)
    return FileResponse(data["html_path"], media_type="text/html")


@app.get("/download/{job_id}/json")
def download_json(job_id: str):
    data = JOB_STATUS.get(job_id)
    if not data or data.get("status") != "DONE":
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)
    return FileResponse(data["json_path"], media_type="application/json", filename="fit_report.json")


@app.get("/download/{job_id}/html")
def download_html(job_id: str):
    data = JOB_STATUS.get(job_id)
    if not data or data.get("status") != "DONE":
        return RedirectResponse(url=f"/wait/{job_id}", status_code=303)
    return FileResponse(data["html_path"], media_type="text/html", filename="report.html")

@app.get("/error/{job_id}", response_class=HTMLResponse)
def page_error(request: Request, job_id: str):
    data = JOB_STATUS.get(job_id, {})
    error_msg = data.get("error") or "An unexpected error occurred during the analysis."

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "job_id": job_id,
            "error": error_msg,
        },
    )

