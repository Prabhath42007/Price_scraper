
import os
import time
import uuid
import logging
import threading
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from browser import get_page
from db import save_to_db, detect_drops
from main import (
    load_products,
    scrape_product,
    clean_results,
    save_data,
    generate_dashboard,
)

# ── Config ────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("API_KEY")  # if unset, auth is disabled (dev only)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./output")).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "reports").mkdir(exist_ok=True)
(OUTPUT_DIR / "dashboards").mkdir(exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "/etc/secrets/gdrive-service-account.json"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("price_monitor_api")

# ── Job tracking (in-memory — resets on restart, see note #4 above) ───────

_jobs: dict[str, dict] = {}
_scrape_lock = threading.Lock()
_lock_holder: Optional[str] = None  # job_id currently holding the lock


def _new_job(kind: str, target: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "id": job_id,
        "kind": kind,
        "target": target,
        "status": "queued",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "summary": None,
    }
    return job_id


def _check_auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


# ── Google Drive upload ─────────────────────────────────────────────────

_drive_service = None
_drive_init_lock = threading.Lock()
_drive_unavailable_reason: Optional[str] = None  # cached after first failed attempt


def _get_drive_service():
    """Lazily builds (and caches) an authenticated Drive API client.
    Returns None if Drive upload isn't configured or credentials are bad —
    callers should treat that as "skip upload", not a hard error."""
    global _drive_service, _drive_unavailable_reason

    if _drive_service is not None:
        return _drive_service
    if _drive_unavailable_reason is not None:
        return None  # already know it's broken, don't retry every call

    if not GOOGLE_DRIVE_FOLDER_ID:
        _drive_unavailable_reason = "GOOGLE_DRIVE_FOLDER_ID not set"
        logger.warning(f"Drive upload disabled: {_drive_unavailable_reason}")
        return None
    if not Path(GOOGLE_SERVICE_ACCOUNT_FILE).exists():
        _drive_unavailable_reason = f"service account file not found at {GOOGLE_SERVICE_ACCOUNT_FILE}"
        logger.warning(f"Drive upload disabled: {_drive_unavailable_reason}")
        return None

    with _drive_init_lock:
        if _drive_service is None:
            try:
                creds = service_account.Credentials.from_service_account_file(
                    GOOGLE_SERVICE_ACCOUNT_FILE,
                    scopes=["https://www.googleapis.com/auth/drive.file"],
                )
                _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
            except Exception as e:
                _drive_unavailable_reason = f"failed to init Drive client: {e}"
                logger.exception("Drive upload disabled: init failed")
                return None
    return _drive_service


def _find_drive_file_id(service, name: str, parent_id: str) -> Optional[str]:
    safe_name = name.replace("'", "\\'")
    query = f"name = '{safe_name}' and '{parent_id}' in parents and trashed = false"
    resp = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def _upload_to_drive(file_path: Path, mime_type: str) -> Optional[str]:
    """Uploads file_path into GOOGLE_DRIVE_FOLDER_ID. If a file with the same
    name already exists there, it's updated in place (so the xlsx report
    keeps accumulating sheets as one Drive file, rather than duplicating on
    every run). Returns the Drive webViewLink, or None if upload was skipped
    or failed — failures never abort the scrape job itself."""
    service = _get_drive_service()
    if service is None:
        return None

    try:
        existing_id = _find_drive_file_id(service, file_path.name, GOOGLE_DRIVE_FOLDER_ID)
        media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=False)

        if existing_id:
            uploaded = service.files().update(
                fileId=existing_id, media_body=media, fields="id, webViewLink"
            ).execute()
        else:
            uploaded = service.files().create(
                body={"name": file_path.name, "parents": [GOOGLE_DRIVE_FOLDER_ID]},
                media_body=media,
                fields="id, webViewLink",
            ).execute()

        link = uploaded.get("webViewLink")
        logger.info(f"Uploaded {file_path.name} to Drive: {link}")
        return link

    except HttpError as e:
        logger.error(f"Drive API error uploading {file_path.name}: {e}")
        return None
    except Exception:
        logger.exception(f"Unexpected error uploading {file_path.name} to Drive")
        return None


# ── Core scrape logic (adapted from main.main(), reused via imports) ──────

def _scrape_one_product(site: str, name: str, products: dict, page, logger) -> list[dict]:
    start, end = 1, 5
    res = []
    for i in range(start, end + 1):
        for attempt in range(2):
            try:
                page_url = products[site]["pages"][name] + str(i)
                page_res = scrape_product(
                    site, page_url, products[site]["container"],
                    products[site]["selectors"], page,
                )
                res.extend(page_res)
                time.sleep(0.5)
                break
            except PlaywrightTimeoutError as e:
                logger.warning(f"Timeout attempt {attempt+1} for {name}-{i}: {e}")
            except Exception as e:
                logger.error(f"Error attempt {attempt+1} for {name}-{i}: {e}")
            time.sleep(0.5)
            page.reload()
        else:
            logger.error(f"Failed to scrape {name}-{i} from {site}")
    return res


def _process_and_save(name: str, res: list[dict]) -> dict:
    res = clean_results(res)
    res = detect_drops(name, res)

    drops = sum(1 for r in res if r.get("Drop Alert") in ("YES", "URGENT"))
    urgent = sum(1 for r in res if r.get("Drop Alert") == "URGENT")
    biggest_drop = max(
        (r.get("Drop %", 0) for r in res if isinstance(r.get("Drop %"), (int, float))),
        default=0,
    )

    dashboard_path = OUTPUT_DIR / "dashboards" / f"{name}_dashboard.png"
    generate_dashboard(len(res), drops, urgent, biggest_drop, str(dashboard_path))

    report_path = OUTPUT_DIR / "reports" / f"{name}_report.xlsx"
    save_data(res, report_path)

    save_to_db(name, res)

    dashboard_drive_url = _upload_to_drive(dashboard_path, "image/png")
    report_drive_url = _upload_to_drive(
        report_path,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    return {
        "products": len(res),
        "drops": drops,
        "urgent": urgent,
        "biggest_drop_pct": round(biggest_drop, 2),
        "report_file": report_path.name,
        "dashboard_file": dashboard_path.name,
        "report_drive_url": report_drive_url,
        "dashboard_drive_url": dashboard_drive_url,
    }


def _run_full_scrape(job_id: str):
    global _lock_holder
    if not _scrape_lock.acquire(blocking=False):
        _jobs[job_id]["status"] = "rejected"
        _jobs[job_id]["error"] = "Another scrape job is already running"
        return

    _lock_holder = job_id
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    playwright = browser = context = None
    all_summaries = {}

    try:
        products = load_products()
        playwright, browser, context = get_page()
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en']});
        """)

        for site in products.keys():
            for name in products[site]["pages"].keys():
                page = context.new_page()
                logger.info(f"[{job_id}] Scraping {name} from {site}...")
                try:
                    res = _scrape_one_product(site, name, products, page, logger)
                finally:
                    page.close()

                if not res:
                    logger.error(f"[{job_id}] No results for {name} on {site}")
                    continue

                all_summaries[f"{site}:{name}"] = _process_and_save(name, res)

        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["summary"] = all_summaries

    except Exception as e:
        logger.exception(f"[{job_id}] Scrape job failed")
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)

    finally:
        try:
            if browser:
                browser.close()
            if playwright:
                playwright.stop()
        except Exception:
            logger.exception(f"[{job_id}] Error during browser cleanup")
        _jobs[job_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _lock_holder = None
        _scrape_lock.release()


def _run_single_scrape(job_id: str, site: str, name: str):
    global _lock_holder
    if not _scrape_lock.acquire(blocking=False):
        _jobs[job_id]["status"] = "rejected"
        _jobs[job_id]["error"] = "Another scrape job is already running"
        return

    _lock_holder = job_id
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    playwright = browser = context = None

    try:
        products = load_products()
        if site not in products or name not in products[site]["pages"]:
            raise ValueError(f"Unknown site/product combo: {site} / {name}")

        playwright, browser, context = get_page()
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en']});
        """)

        page = context.new_page()
        try:
            res = _scrape_one_product(site, name, products, page, logger)
        finally:
            page.close()

        if not res:
            raise RuntimeError(f"No results scraped for {name} on {site}")

        summary = _process_and_save(name, res)
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["summary"] = summary

    except Exception as e:
        logger.exception(f"[{job_id}] Single scrape job failed")
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)

    finally:
        try:
            if browser:
                browser.close()
            if playwright:
                playwright.stop()
        except Exception:
            logger.exception(f"[{job_id}] Error during browser cleanup")
        _jobs[job_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _lock_holder = None
        _scrape_lock.release()


# ── FastAPI app ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Price monitor API starting up")
    yield
    logger.info("Price monitor API shutting down")


app = FastAPI(title="Triggrr Price Monitor API", lifespan=lifespan)


@app.get("/health")
def health():
    """Render uses this (or any 200 response) as the health check target."""
    drive_ok = _get_drive_service() is not None
    return {
        "status": "ok",
        "lock_held_by": _lock_holder,
        "drive_upload_enabled": drive_ok,
        "drive_upload_issue": None if drive_ok else _drive_unavailable_reason,
    }


@app.post("/scrape/all", status_code=202)
def scrape_all(background_tasks: BackgroundTasks, x_api_key: Optional[str] = Header(None)):
    _check_auth(x_api_key)
    if _scrape_lock.locked():
        raise HTTPException(status_code=409, detail=f"Job {_lock_holder} is already running")

    job_id = _new_job(kind="full", target="all")
    background_tasks.add_task(_run_full_scrape, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/scrape/{site}/{product}", status_code=202)
def scrape_single(
    site: str,
    product: str,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(None),
):
    _check_auth(x_api_key)
    if _scrape_lock.locked():
        raise HTTPException(status_code=409, detail=f"Job {_lock_holder} is already running")

    job_id = _new_job(kind="single", target=f"{site}:{product}")
    background_tasks.add_task(_run_single_scrape, job_id, site, product)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    return job


@app.get("/jobs")
def list_jobs():
    return {"jobs": sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)}


@app.get("/reports/{filename}")
def get_report(filename: str):
    path = OUTPUT_DIR / "reports" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@app.get("/dashboards/{filename}")
def get_dashboard(filename: str):
    path = OUTPUT_DIR / "dashboards" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(path, media_type="image/png", filename=filename)


@app.get("/")
def root():
    return JSONResponse({
        "service": "Triggrr Price Monitor API",
        "endpoints": [
            "GET  /health",
            "POST /scrape/all            (X-API-Key header required)",
            "POST /scrape/{site}/{product} (X-API-Key header required)",
            "GET  /jobs",
            "GET  /jobs/{job_id}",
            "GET  /reports/{filename}",
            "GET  /dashboards/{filename}",
        ],
    })

    

    

