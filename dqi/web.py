"""Local browser interface backed by the same inspection engine as the CLI."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sys
from tempfile import TemporaryDirectory
import threading
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser
import zipfile
import zlib

from .__main__ import export, write_review_queue
from .core import Config, Dataset, inspect, load_dataset
from .audit import add_audit
from .json_utils import dumps_json, loads_json
from .presets import HELPSTEER2
from .report_appearance import with_report_theme
from .review_cost import MODEL_RATES, estimate_review_cost
from .rubric_import import MAX_RUBRIC_BYTES, parse_rubric
from .semantic import MAX_REVIEW_RECORDS, RUBRIC_VERSION, HELPSTEER_RUBRICS, prepare_review, review_record, review_skip_reason, summarize_review
from .semantic_provider import DEFAULT_MODEL, MAX_OUTPUT_TOKENS, ProviderError, make_judge
from .semantic_reporting import export_review
from .single_task import TASK_FIELDS, prepare_single_task
from . import credentials, shortcuts


VERSION = "0.15.0"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_UPLOADS = 8
MAX_REVIEW_JOBS = 8
MAX_REVIEW_REQUESTS = 256
MAX_REVIEW_QUOTES = 32
REVIEW_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
PREVIEW_ROWS = 5
PREVIEW_CHARS = 300
STATIC = Path(__file__).with_name("static")
STATIC_FILES = {
    "index.html": "text/html", "styles.css": "text/css",
    "dark.css": "text/css", "appearance.js": "application/javascript",
    "app.js": "application/javascript", "audit-ui.js": "application/javascript",
    "semantic-ui.js": "application/javascript", "exact-json.js": "application/javascript",
    "rubric-import.js": "application/javascript", "rubric-template.csv": "text/csv",
    "rubric-template.json": "application/json", "single-task.js": "application/javascript",
    "settings.js": "application/javascript", "settings.css": "text/css", "walkthrough.css": "text/css",
    "guided-tour.js": "application/javascript", "tour-overlay.js": "application/javascript",
    "tour-stops.js": "application/javascript", "tour-preview.js": "application/javascript", "tour.css": "text/css",
}
REPORT_FILES = {
    "summary.html": ("text/html; charset=utf-8", "inline"),
    "summary.txt": ("text/plain; charset=utf-8", "attachment"),
    "report.json": ("application/json; charset=utf-8", "attachment"),
    "findings.csv": ("text/csv; charset=utf-8", "attachment"),
    "skipped-checks.csv": ("text/csv; charset=utf-8", "attachment"),
    "review-queue.csv": ("text/csv; charset=utf-8", "attachment"),
    "report.zip": ("application/zip", "attachment"),
    "answer-review.html": ("text/html; charset=utf-8", "inline"),
    "answer-review.json": ("application/json; charset=utf-8", "attachment"),
    "answer-review.csv": ("text/csv; charset=utf-8", "attachment"),
    "answer-review.zip": ("application/zip", "attachment"),
}


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)


def filtered_review_csv(snapshot, reason, attribute):
    queue = loads_json(snapshot)["audit"]["review_queue"]
    matching_rows = (row for row in queue if any(
        (not reason or item["kind"] == reason) and (not attribute or item["field"] == attribute)
        for item in row["reasons"]
    ))
    with StringIO(newline="") as output:
        write_review_queue(output, matching_rows)
        return output.getvalue().encode("utf-8")


@dataclass
class Upload:
    upload_id: str
    filename: str
    path: Path
    directory: Path
    dataset: Dataset
    report_id: str | None = None


@dataclass(frozen=True)
class ReviewQuote:
    settings_digest: str
    rubric_sha256: str
    estimate: dict


def _review_settings_digest(payload):
    # Report IDs identify immutable input and rule snapshots for this session.
    settings = [payload.get("report_id"), payload.get("options"), payload.get("model", DEFAULT_MODEL), payload.get("rates")]
    return hashlib.sha256(dumps_json(settings, indent=None, sort_keys=True).encode()).hexdigest()


@dataclass
class ReviewJob:
    job_id: str
    upload_id: str | None
    total: int
    cancel: threading.Event
    request_id: str = ""
    status: str = "running"
    processed: int = 0
    result: dict | None = None
    urls: dict | None = None
    error: str | None = None
    parent_job_id: str | None = None
    opinion_record: int | None = None
    opinion_model: str | None = None
    review_mode: str | None = None
    task_digest: str | None = None
    quote_id: str | None = None
    settings_digest: str | None = None

    def public(self):
        value = {"job_id": self.job_id, "request_id": self.request_id, "status": self.status, "processed": self.processed, "total": self.total, "cancel_requested": self.cancel.is_set()}
        for name in ("result", "urls", "error", "parent_job_id", "review_mode"):
            if getattr(self, name) is not None:
                value[name] = getattr(self, name)
        return value


class AppState:
    def __init__(self):
        self.temporary = TemporaryDirectory(prefix="dqi-web-")
        self.directory = Path(self.temporary.name)
        self.token = secrets.token_urlsafe(32)
        self.uploads: OrderedDict[str, Upload] = OrderedDict()
        self.reports: dict[str, Path] = {}
        self.jobs: OrderedDict[str, ReviewJob] = OrderedDict()
        self.review_quotes: OrderedDict[str, ReviewQuote] = OrderedDict()
        # Tombstones survive report eviction so an ambiguous retry cannot bill twice.
        self.review_requests: dict[str, str] = {}
        self.lock = threading.RLock()
        self.closed = False

    def add(self, upload):
        with self.lock:
            if self.closed:
                raise ApiError(503, "The inspector is closing. Reopen it to continue.")
            while len(self.uploads) >= MAX_UPLOADS:
                pinned = {job.upload_id for job in self.jobs.values() if job.status == "running"}
                oldest = next((key for key in self.uploads if key not in pinned), None)
                if oldest is None:
                    raise ApiError(409, "Wait for the answer review to finish before uploading another file.")
                old = self.uploads.pop(oldest)
                if old.report_id:
                    self.reports.pop(old.report_id, None)
                shutil.rmtree(old.directory, ignore_errors=True)
            self.uploads[upload.upload_id] = upload

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                job.cancel.set()
            self.uploads.clear()
            self.reports.clear()
            self.jobs.clear()
            self.review_requests.clear()
            self.review_quotes.clear()
            self.temporary.cleanup()

    def job_to_expire(self, parent_job_id):
        """Choose a completed job without orphaning a retained second opinion."""
        if len(self.jobs) < MAX_REVIEW_JOBS:
            return None
        protected = {job.parent_job_id for job in self.jobs.values()}
        protected.add(parent_job_id)
        for job_id, job in self.jobs.items():
            if job_id not in protected and job.status != "running":
                return job_id
        raise ApiError(409, "There is no space to retain both reviews in this session.")


def _second_opinion_plan(parent, number, model):
    """Use the frozen primary report to reproduce one blind assessment request."""
    if parent.parent_job_id is not None:
        raise ApiError(400, "Choose a record from a primary answer review.")
    result = parent.result
    if not result or parent.status == "running":
        raise ApiError(409, "The original review has no finished results to compare.")
    if model == result["model"] or model in result.get("actual_models", []):
        raise ApiError(400, "Choose a different model for the second opinion.")
    row = next((row for row in result["rows"] if row["record"] == number), None)
    if not row or row["status"] not in {"reviewed", "insufficient_evidence"} or not row["assessments"]:
        raise ApiError(400, "Select a record with an assessment from the primary reviewer.")
    if not result.get("reviewer_instructions"):
        raise ApiError(409, "The original reviewer instructions are unavailable. Run a new primary review.")

    options = result["options"].copy()
    context = {key: row[key] for key in ("record", "source_line", "prompt", "response", "reference")}
    plan = {key: result[key] for key in ("attributes", "total_records", "rubric_version", "rubric_sha256",
                                          "reviewer_instructions", "instructions_sha256")}
    if parent.review_mode == "single_task":
        plan["review_mode"] = context["review_mode"] = "single_task"
        plan["rubric_source"] = result["source"].get("rubric_filename") or "User-supplied rubric"
        context["original_ratings"] = {}
    else:
        options.update(selection="records", limit=1, records=[number])
        context.update(original_ratings=row["original_ratings"], tolerance=options["tolerance"])
    plan.update(options=options, records=[context], selected_count=1,
                selection_note=f"One answer selected for a second opinion on record {number}.",
                primary_review={"job_id": parent.job_id, "model": result["model"], "record": number,
                                "assessments": row["assessments"]})
    return plan


def _run_answer_review(state, job, plan, judge, model, source):
    """Network work stays outside the state lock; one call at a time, no retry."""
    rows, usage, actual_models = [], {"input_tokens": 0, "output_tokens": 0}, set()
    usage_complete = True
    status, error = "completed", None
    try:
        for context in plan["records"]:
            with state.lock:
                if state.closed or job.cancel.is_set():
                    status = "cancelled"
                    break
            try:
                row = review_record(context, plan["attributes"], judge,
                                    reviewer_instructions=plan["reviewer_instructions"])
            except ProviderError as exc:
                error, status = str(exc), "failed"
                row = {key: value for key, value in context.items() if key not in {"tolerance", "skip_reason"}}
                row.update(status="error", assessments=[], error=error)
            except Exception:
                error, status = "The answer review stopped because this record could not be processed. Completed results are retained.", "failed"
                row = {key: value for key, value in context.items() if key not in {"tolerance", "skip_reason"}}
                row.update(status="error", assessments=[], error=error)
            rows.append(row)
            if row.get("status") != "skipped":
                reported_usage = getattr(judge, "last_usage", {})
                if not isinstance(reported_usage, dict):
                    reported_usage = {}
                if not all(type(reported_usage.get(key)) is int and reported_usage[key] >= 0 for key in usage):
                    usage_complete = False
                for key in usage:
                    value = reported_usage.get(key, 0)
                    if type(value) is int and value >= 0:
                        usage[key] += value
                actual = getattr(judge, "last_model", None)
                if getattr(judge, "last_response_received", False) and isinstance(actual, str):
                    actual_models.add(actual)
            with state.lock:
                if state.closed:
                    return
                job.processed = len(rows)
            if status == "failed":
                break
        with state.lock:
            if state.closed:
                return
            if job.cancel.is_set() and status != "failed":
                status = "cancelled"
        result = summarize_review(plan, rows, provider="openai", model=model)
        if "cost_estimate" in plan:
            result["cost_estimate"] = plan["cost_estimate"]
        if "primary_review" in plan:
            result["primary_review"] = plan["primary_review"]
        usage_note = "Token totals include usage reported by the provider; they are not a price or billing estimate." if usage_complete else "Token totals are incomplete: one or more attempted requests did not report usage and may still incur charges. Check your API Usage dashboard before starting another review."
        result.update(source=source, run_status=status, usage=usage, usage_complete=usage_complete, usage_note=usage_note, actual_models=sorted(actual_models), request_id=job.request_id, completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), max_output_tokens_per_request=MAX_OUTPUT_TOKENS)
        if error:
            result["error"] = error
        # Full-dataset bundles can take time to serialize. Stage them outside
        # the session so polling and closing still work during the export.
        try:
            with TemporaryDirectory(dir=state.directory.parent, prefix="dqi-export-") as staging:
                staged = Path(staging) / "report"
                export_review(result, staged)
                with state.lock:
                    if state.closed:
                        return
                    folder = state.directory / "answer-reviews" / job.job_id
                    folder.parent.mkdir(exist_ok=True)
                    staged.replace(folder)
                    state.reports[job.job_id] = folder
                    job.urls = {kind: f"/api/reports/{job.job_id}/answer-review.{kind}" for kind in ("html", "json", "csv", "zip")}
        except Exception:
            status = "failed"
            error = "The review finished but its downloadable files could not be saved. The results remain available in this session."
            result.update(run_status=status, error=error)
        with state.lock:
            if not state.closed:
                job.result, job.status, job.error = result, status, error
    except Exception:
        with state.lock:
            if not state.closed:
                job.status = "failed"
                job.error = "The answer review could not finish. No automatic retry was made."
    finally:
        close = getattr(judge, "close", None)
        if callable(close):
            close()


def _preview(value):
    """Bound previews only; the stored dataset retains the original values."""
    if isinstance(value, str):
        return (value[:PREVIEW_CHARS - 1] + "…", True) if len(value) > PREVIEW_CHARS else (value, False)
    if isinstance(value, (list, dict)):
        text = dumps_json(value, indent=None)
        return (text[:PREVIEW_CHARS - 1] + "…", True) if len(text) > PREVIEW_CHARS else (value, False)
    return value, False


def _upload_preview(upload):
    rows, truncated = [], len(upload.dataset.records) > PREVIEW_ROWS
    for record in upload.dataset.records[:PREVIEW_ROWS]:
        values = {}
        for key, value in record.values.items():
            values[key], shortened = _preview(value)
            truncated |= shortened
        rows.append({"record": record.number, "values": values})
    return {
        "upload_id": upload.upload_id,
        "filename": upload.filename,
        "input_format": upload.dataset.input_format,
        "record_count": len(upload.dataset.records),
        "columns": list(upload.dataset.headers),
        "preview": rows,
        "preview_truncated": truncated,
    }


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.state = AppState()

    def server_close(self):
        super().server_close()
        state = getattr(self, "state", None)
        if state is not None:
            state.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "DataQualityInspector"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, _format, *_args):
        # Upload names, dataset content, tokens and request payloads stay out of logs.
        pass

    @property
    def state(self):
        return self.server.state

    def _hosts(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _check_host(self):
        values = self.headers.get_all("Host", [])
        if len(values) != 1 or values[0].strip().lower() not in self._hosts():
            raise ApiError(403, "Use the inspector's localhost address.")

    def _check_mutation(self):
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] not in {"http://" + host for host in self._hosts()}):
            raise ApiError(403, "This request did not originate from the local inspector.")
        tokens = self.headers.get_all("X-DQI-Token", [])
        if len(tokens) != 1 or not tokens[0].isascii() or not secrets.compare_digest(tokens[0], self.state.token):
            raise ApiError(403, "Reload the inspector page before trying again.")

    def _send(self, status, body, content_type="application/json; charset=utf-8", disposition=None):
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, dumps_json(payload).encode("utf-8"))

    def _length(self, maximum):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "Chunked request bodies are not supported.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].strip().isdigit():
            raise ApiError(400, "A valid Content-Length is required.")
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise ApiError(400, "A valid Content-Length is required.") from exc
        if length > maximum:
            limit = f"{maximum // 1024} KiB" if maximum < 1024 * 1024 else f"{maximum // (1024 * 1024)} MB"
            raise ApiError(413, f"This request exceeds the {limit} limit.")
        return length

    def _read(self, maximum):
        length = self._length(maximum)
        data = self.rfile.read(length)
        if len(data) != length:
            raise ApiError(400, "The request ended before all data arrived.")
        return data

    def _dispatch(self, method):
        try:
            self._check_host()
            if method == "POST":
                self._check_mutation()
                self._post()
            else:
                self._get()
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message})
        except (socket.timeout, TimeoutError):
            self._json(408, {"error": "The request timed out. Please try again."})
        except (ValueError, UnicodeError, RecursionError) as exc:
            self._json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500, {"error": "The inspector could not complete this request. Please retry or reopen the app."})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _get(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json(200, {"app": "data-quality-inspector", "version": VERSION})
        elif path == "/tour-example.json":
            from .tour_example import tour_example
            self._json(200, tour_example(VERSION))
        elif path == "/api/settings/api-key":
            self._check_mutation()
            self._json(200, credentials.key_status())
        elif path == "/api/bootstrap":
            with self.state.lock:
                active = next((job.job_id for job in self.state.jobs.values() if job.status == "running"), None)
                latest = next(reversed(self.state.jobs), None)
                latest_request = self.state.jobs[latest].request_id if latest else None
                restore_mode = self.state.jobs[active or latest].review_mode if (active or latest) else None
            self._json(200, {"csrf_token": self.state.token, "max_upload_bytes": MAX_UPLOAD_BYTES, "filtered_review_csv": True, "supported_formats": ["csv", "tsv", "json", "jsonl", "ndjson"], "presets": {"helpsteer2": HELPSTEER2}, "desktop_shortcut": shortcuts.desktop_shortcut_status(), "semantic": {"rubric_version": RUBRIC_VERSION, "helpsteer_rubrics": HELPSTEER_RUBRICS, "default_model": DEFAULT_MODEL, "max_records": MAX_REVIEW_RECORDS, "model_rates": MODEL_RATES, "max_output_tokens_per_request": MAX_OUTPUT_TOKENS, "active_job_id": active, "latest_job_id": latest, "latest_request_id": latest_request, "restore_review_mode": restore_mode}})
        elif path == "/tour-preview":
            shell = (STATIC / "index.html").read_text(encoding="utf-8")
            preview = re.sub(r"<!-- guided tour controls -->.*?<!-- end guided tour controls -->",
                             '<script src="./tour-preview.js" defer></script>', shell, flags=re.DOTALL)
            self._send(200, preview.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/" or path[1:] in STATIC_FILES:
            name = "index.html" if path == "/" else path[1:]
            file = STATIC / name
            if not file.is_file():
                raise ApiError(404, "The interface file is not available.")
            disposition = f'attachment; filename="{name}"' if name.startswith("rubric-template.") else None
            self._send(200, file.read_bytes(), STATIC_FILES[name] + "; charset=utf-8", disposition)
        elif path.startswith("/api/answer-review/"):
            self._check_mutation()
            parts = path.split("/")
            if len(parts) == 5 and parts[3] == "request":
                with self.state.lock:
                    job_id = self.state.review_requests.get(parts[4])
                    if job_id is None:
                        raise ApiError(404, "No answer review was started for this request.")
                    job = self.state.jobs.get(job_id)
                    if job is None:
                        raise ApiError(409, "This request was already processed, but its results have expired. It will not be run again automatically.")
                    public = job.public()
                self._json(200, public)
                return
            if len(parts) != 4:
                raise ApiError(404, "Answer review not found.")
            with self.state.lock:
                job = self.state.jobs.get(parts[3])
                if job is None:
                    raise ApiError(404, "This answer review is no longer available.")
                public = job.public()
            self._json(200, public)
        elif path.startswith("/api/reports/"):
            parts = path.split("/")
            if len(parts) != 5 or parts[4] not in REPORT_FILES:
                raise ApiError(404, "Report file not found.")
            report_id, name = parts[3:]
            reason = attribute = ""
            if name == "review-queue.csv":
                fields = parse_qs(parsed.query, keep_blank_values=True)
                if any(len(fields.get(key, [])) > 1 for key in ("reason", "attribute")):
                    raise ApiError(400, "Choose one reason and one attribute per download.")
                reason = fields.get("reason", [""])[0]
                attribute = fields.get("attribute", [""])[0]
            with self.state.lock:
                folder = self.state.reports.get(report_id)
                if folder is None or not (folder / name).is_file():
                    raise ApiError(404, "This report is no longer available. Run the inspection again.")
                source = folder / ("report.json" if reason or attribute else name)
                body = source.read_bytes()
            content_type, mode = REPORT_FILES[name]
            if reason or attribute:
                body = filtered_review_csv(body, reason, attribute)
                name = "review-queue-filtered.csv"
            if mode == "inline":
                choice = re.search(r"(?:^|;\s*)dqi_appearance=(dark|light)(?=;|$)", self.headers.get("Cookie", ""))
                if choice:
                    body = with_report_theme(body, choice[1])
            self._send(200, body, content_type, f'{mode}; filename="{name}"')
        else:
            raise ApiError(404, "Page not found.")

    def _post(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/upload":
            self._upload(parse_qs(parsed.query, keep_blank_values=True))
        elif parsed.path == "/api/rubric-import":
            name = self._upload_filename()
            self._json(200, parse_rubric(name, self._read(MAX_RUBRIC_BYTES)))
        elif parsed.path == "/api/inspect":
            self._inspect()
        elif parsed.path == "/api/settings/api-key":
            self._api_key_settings()
        elif parsed.path == "/api/answer-review/preview":
            self._answer_review_preview()
        elif parsed.path == "/api/answer-review":
            self._answer_review_start()
        elif parsed.path == "/api/single-task/preview":
            self._answer_review_preview(single_task=True)
        elif parsed.path == "/api/single-task/review":
            self._answer_review_start(single_task=True)
        elif parsed.path == "/api/answer-review/second-opinion":
            self._answer_review_start(second_opinion=True)
        elif parsed.path.startswith("/api/answer-review/") and parsed.path.endswith("/cancel"):
            self._answer_review_cancel(parsed.path)
        elif parsed.path == "/api/desktop-shortcut":
            if parsed.query:
                raise ApiError(400, "The Desktop shortcut uses a fixed location; no options are accepted.")
            self._desktop_shortcut()
        elif parsed.path == "/api/close":
            try:
                self._json(200, {"closed": True})
            finally:
                # Reply first; the serving loop's owner then closes temporary storage.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            raise ApiError(404, "Action not found.")

    def _api_key_settings(self):
        if self.headers.get_content_type() != "application/json" or urlsplit(self.path).query:
            raise ApiError(400, "Send the API key settings in a JSON request body.")
        try:
            payload = loads_json(self._read(32 * 1024).decode("utf-8"))
        except (ValueError, RecursionError):
            raise ApiError(400, "The API key settings must be valid JSON.") from None
        if isinstance(payload, dict) and set(payload) == {"api_key"}:
            self._json(200, credentials.save_key(payload["api_key"]))
        elif payload == {"action": "remove"}:
            self._json(200, credentials.remove_key())
        else:
            raise ApiError(400, "Supply an API key to save or choose the remove action.")

    def _review_payload(self, allowed):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(400, "Answer-review settings must be sent as JSON.")
        try:
            payload = loads_json(self._read(MAX_CONFIG_BYTES).decode("utf-8-sig"))
        except (ValueError, RecursionError):
            raise ApiError(400, "Answer-review settings must be valid JSON.") from None
        if not isinstance(payload, dict) or set(payload) - set(allowed):
            raise ApiError(400, "Provide only the supported answer-review settings.")
        return payload

    def _review_plan(self, payload):
        """Called under the state lock; preserves the exact selected report rules."""
        if self.state.closed:
            raise ApiError(503, "The inspector is closing. Reopen it to continue.")
        report_id = payload.get("report_id")
        if not isinstance(report_id, str):
            raise ApiError(400, "Run the data inspection before reviewing answers.")
        upload = next((item for item in self.state.uploads.values() if item.report_id == report_id), None)
        folder = self.state.reports.get(report_id)
        if upload is None or folder is None:
            raise ApiError(404, "This inspection is no longer available. Run the data inspection again.")
        report = loads_json((folder / "report.json").read_text(encoding="utf-8"))
        plan = prepare_review(upload.dataset, report, payload.get("options"))
        return upload, report, plan

    def _answer_review_preview(self, *, single_task=False):
        payload = self._review_payload(TASK_FIELDS if single_task else {"report_id", "options", "model", "rates"})
        with self.state.lock:
            if single_task:
                if self.state.closed:
                    raise ApiError(503, "The inspector is closing. Reopen it to continue.")
                plan = prepare_single_task(payload)
            else:
                _, _, plan = self._review_plan(payload)
        skipped = sum(bool(row.get("skip_reason")) for row in plan["records"])
        response = {"selected_count": plan["selected_count"], "total_records": plan["total_records"],
                    "record_numbers": [row["record"] for row in plan["records"][:MAX_REVIEW_RECORDS]],
                    "selection_note": plan["selection_note"], "eligible_count": plan["selected_count"] - skipped,
                    "skipped_count": skipped, "max_output_tokens_per_request": MAX_OUTPUT_TOKENS}
        if not single_task:
            estimate = estimate_review_cost(plan, payload.get("model", DEFAULT_MODEL), payload.get("rates"))
            quote_id = secrets.token_urlsafe(24)
            quote = ReviewQuote(_review_settings_digest(payload), plan["rubric_sha256"], estimate)
            with self.state.lock:
                if payload["report_id"] not in self.state.reports:
                    raise ApiError(409, "This inspection changed while estimating. Preview the current report again.")
                self.state.review_quotes[quote_id] = quote
                if len(self.state.review_quotes) > MAX_REVIEW_QUOTES:
                    self.state.review_quotes.popitem(last=False)
            response.update(estimate=estimate, quote_id=quote_id)
        self._json(200, response)

    def _answer_review_start(self, *, second_opinion=False, single_task=False):
        dataset_review = not (single_task or second_opinion)
        fields = {"report_id", "options", "quote_id", "rates"}
        if single_task:
            fields = TASK_FIELDS
        elif second_opinion:
            fields = {"parent_job_id", "record"}
        payload = self._review_payload(fields | {"model", "api_key", "use_saved_key", "consent", "request_id"})
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not REVIEW_REQUEST_ID.fullmatch(request_id):
            raise ApiError(400, "A unique request ID is required to prevent duplicate answer-review charges. Reload the page and try again.")
        if payload.get("consent") is not True:
            raise ApiError(400, "Confirm sending the selected questions, answers, references, and rubric to OpenAI before starting a billed review.")
        use_saved_key = payload.pop("use_saved_key", False)
        if type(use_saved_key) is not bool or (use_saved_key and "api_key" in payload):
            raise ApiError(400, "Choose either the saved API key or a key for this review.")
        api_key = payload.pop("api_key", None)
        model = payload.get("model", DEFAULT_MODEL)
        parent_job_id = payload.get("parent_job_id")
        record = payload.get("record")
        quote_id = payload.get("quote_id")
        settings_digest = _review_settings_digest(payload) if dataset_review else None
        task_digest = None
        if single_task:
            plan = prepare_single_task(payload)
            identity = [model, plan["records"], plan["rubric_sha256"], plan["rubric_source"]]
            task_digest = hashlib.sha256(dumps_json(identity, indent=None).encode()).hexdigest()
        if second_opinion:
            if not isinstance(parent_job_id, str) or type(record) is not int or record < 1:
                raise ApiError(400, "Select a primary review and one positive record number.")
            if not isinstance(payload.get("model"), str) or not model.strip():
                raise ApiError(400, "Choose a model for the second opinion.")
        judge = None
        try:
            with self.state.lock:
                if self.state.closed:
                    raise ApiError(503, "The inspector is closing. Reopen it to continue.")
                previous_id = self.state.review_requests.get(request_id)
                if previous_id is not None:
                    if previous_id not in self.state.jobs:
                        raise ApiError(409, "This request was already processed, but its results have expired. It will not be run again automatically.")
                    previous = self.state.jobs[previous_id]
                    if (previous.parent_job_id != parent_job_id or
                        previous.task_digest != task_digest or
                        previous.quote_id != quote_id or previous.settings_digest != settings_digest or
                        (second_opinion and (previous.opinion_record != record or previous.opinion_model != model))):
                        raise ApiError(409, "This request ID already identifies another review.")
                    self._json(202, {"job_id": previous_id, "request_id": request_id})
                    return
                if any(job.status == "running" for job in self.state.jobs.values()):
                    raise ApiError(409, "An answer review is already running. Wait for it or cancel it before starting another.")
                if len(self.state.review_requests) >= MAX_REVIEW_REQUESTS:
                    raise ApiError(409, "This local session has reached its answer-review limit. Download your reports, then close and reopen the inspector to start a new session.")
                if single_task:
                    upload_id = None
                    source = {"kind": "single_task", "filename": "Pasted task", "app_version": VERSION}
                    if payload.get("rubric_filename"):
                        source["rubric_filename"] = payload["rubric_filename"]
                elif second_opinion:
                    parent = self.state.jobs.get(parent_job_id)
                    if parent is None:
                        raise ApiError(404, "This primary review is no longer available in this session.")
                    plan = _second_opinion_plan(parent, record, model)
                    upload_id = parent.upload_id
                    source = parent.result["source"]
                else:
                    quote = self.state.review_quotes.get(quote_id) if isinstance(quote_id, str) else None
                    if quote is None or quote.settings_digest != settings_digest:
                        raise ApiError(409, "Preview a new cost estimate for these settings and confirm it before starting.")
                    upload, _, plan = self._review_plan(payload)
                    if quote.rubric_sha256 != plan["rubric_sha256"]:
                        raise ApiError(409, "The reviewer instructions have changed. Preview and confirm a new estimate.")
                    plan["cost_estimate"] = quote.estimate
                    upload_id = upload.upload_id
                    source = {"filename": upload.filename, "sha256": hashlib.sha256(upload.path.read_bytes()).hexdigest(), "app_version": VERSION, "data_report_id": upload.report_id}
                if not any(not review_skip_reason(row) for row in plan["records"]):
                    raise ApiError(400, "None of the selected records has a complete question and answer within the review size limit. Choose other records.")
                old_id = self.state.job_to_expire(parent_job_id)
                try:
                    if use_saved_key:
                        api_key = credentials.load_key()
                    judge = make_judge(api_key=api_key, model=model)
                except ProviderError as exc:
                    raise ApiError(400, str(exc)) from None
                job = ReviewJob(secrets.token_urlsafe(24), upload_id, plan["selected_count"], threading.Event(),
                                request_id=request_id, parent_job_id=parent_job_id, opinion_record=record,
                                opinion_model=model if second_opinion else None,
                                review_mode=plan.get("review_mode"), task_digest=task_digest,
                                quote_id=quote_id, settings_digest=settings_digest)
                self.state.jobs[job.job_id] = job
                worker = threading.Thread(target=_run_answer_review, args=(self.state, job, plan, judge, model, source), daemon=True)
                try:
                    worker.start()
                    self.state.review_requests[request_id] = job.job_id
                    if dataset_review:
                        self.state.review_quotes.pop(quote_id)
                except Exception:
                    self.state.jobs.pop(job.job_id, None)
                    raise
                judge = None  # The worker now owns and clears the provider credential.
                if old_id is not None:
                    self.state.jobs.pop(old_id)
                    old_folder = self.state.reports.pop(old_id, None)
                    if old_folder is not None:
                        shutil.rmtree(old_folder, ignore_errors=True)
        finally:
            api_key = None
            if judge is not None:
                close = getattr(judge, "close", None)
                if callable(close):
                    close()
        self._json(202, {"job_id": job.job_id, "request_id": request_id})

    def _answer_review_cancel(self, path):
        parts = path.split("/")
        if len(parts) != 5:
            raise ApiError(404, "Answer review not found.")
        self._review_payload(set())
        with self.state.lock:
            job = self.state.jobs.get(parts[3])
            if job is None:
                raise ApiError(404, "This answer review is no longer available.")
            if job.status == "running":
                job.cancel.set()
            response = job.public()
        self._json(200, response)

    def _desktop_shortcut(self):
        body = self._read(MAX_CONFIG_BYTES)
        if body:
            if self.headers.get_content_type() != "application/json":
                raise ApiError(400, "The Desktop shortcut request accepts an empty JSON object only.")
            payload = loads_json(body.decode("utf-8-sig"))
            if not isinstance(payload, dict) or payload:
                raise ApiError(400, "The Desktop shortcut uses a fixed location; no options are accepted.")
        try:
            with self.state.lock:
                result = shortcuts.create_desktop_shortcut()
        except shortcuts.DesktopShortcutError as exc:
            self._json(exc.status_code, {"error": str(exc), "code": exc.code})
            return
        self._json(200, result)

    def _upload_filename(self):
        if self.headers.get_content_type() != "application/octet-stream":
            raise ApiError(400, "Upload the file as application/octet-stream.")
        names = self.headers.get_all("X-Filename", [])
        if len(names) != 1:
            raise ApiError(400, "A filename is required.")
        name = unquote(names[0], encoding="utf-8", errors="strict")
        if not name or len(name.encode("utf-8")) > 255 or name in {".", ".."} or any(char in name for char in ("/", "\\")) or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ApiError(400, "Choose a file with a valid filename.")
        return name

    def _upload(self, query):
        name = self._upload_filename()
        formats = query.get("format", [])
        if len(formats) > 1:
            raise ApiError(400, "Choose one file format.")
        selected_format = formats[0] if formats and formats[0] else None
        length = self._length(MAX_UPLOAD_BYTES)
        token = secrets.token_urlsafe(24)
        directory = self.state.directory / token
        directory.mkdir()
        source = directory / name
        registered = False
        try:
            with source.open("wb") as handle:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ApiError(400, "The upload ended before all file data arrived.")
                    handle.write(chunk)
                    remaining -= len(chunk)
            if source.suffix.lower() == ".gz":
                try:
                    with gzip.open(source, "rb") as compressed:
                        expanded = 0
                        while chunk := compressed.read(65536):
                            expanded += len(chunk)
                            if expanded > MAX_EXPANDED_BYTES:
                                raise ApiError(413, "The decompressed file exceeds the 100 MB limit.")
                except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
                    raise ApiError(400, "The gzip file is invalid or incomplete.") from exc
            dataset = load_dataset(source, format=selected_format)
            upload = Upload(token, name, source, directory, dataset)
            payload = _upload_preview(upload)
            self.state.add(upload)
            registered = True
            self._json(200, payload)
        finally:
            if not registered:
                shutil.rmtree(directory, ignore_errors=True)

    def _inspect(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(400, "Inspection settings must be sent as JSON.")
        payload = loads_json(self._read(MAX_CONFIG_BYTES).decode("utf-8-sig"))
        if not isinstance(payload, dict) or set(payload) - {"upload_id", "config", "audit"}:
            raise ApiError(400, "Provide an upload ID and a rules configuration.")
        upload_id = payload.get("upload_id")
        if not isinstance(upload_id, str):
            raise ApiError(400, "A valid upload ID is required.")
        config = Config.from_dict(payload.get("config", {}))
        if "audit" in payload and not isinstance(payload["audit"], dict):
            raise ApiError(400, "Audit options must be a JSON object.")
        with self.state.lock:
            upload = self.state.uploads.get(upload_id)
            if upload is None:
                raise ApiError(404, "This upload is no longer available. Please upload the file again.")
            if any(job.upload_id == upload_id and job.status == "running" for job in self.state.jobs.values()):
                raise ApiError(409, "Wait for this file's answer review to finish or cancel it before changing its inspection.")
            result = inspect(upload.dataset, config)
            if "audit" in payload:
                result = add_audit(upload.dataset, result, payload["audit"])
                with upload.path.open("rb") as original:
                    fingerprint = hashlib.file_digest(original, "sha256").hexdigest()
                result = replace(result, audit={**result.audit, "source": {
                    "filename": upload.filename,
                    "sha256": fingerprint,
                    "app_version": VERSION,
                    "inspected_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                }})
            report_id = secrets.token_urlsafe(24)
            folder = upload.directory / "reports" / report_id
            try:
                export(result, folder, upload.path)
                report = result.to_dict()
                names = [name for name in REPORT_FILES if name != "report.zip" and not name.startswith("answer-review.") and (name != "review-queue.csv" or "audit" in report)]
                with zipfile.ZipFile(folder / "report.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name in names:
                        archive.write(folder / name, arcname=name)
                downloads = [("html", "summary.html"), ("text", "summary.txt"), ("json", "report.json"), ("findings", "findings.csv"), ("skipped", "skipped-checks.csv"), ("zip", "report.zip")]
                if "audit" in report:
                    downloads.append(("review", "review-queue.csv"))
                response = {"report_id": report_id, "filename": upload.filename, "report": report, "urls": {key: f"/api/reports/{report_id}/{name}" for key, name in downloads}}
                if upload.report_id:
                    previous = self.state.reports.pop(upload.report_id, None)
                    if previous is not None:
                        shutil.rmtree(previous, ignore_errors=True)
                upload.report_id = report_id
                self.state.reports[report_id] = folder
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        self._json(200, response)


def make_server(host="127.0.0.1", port=0):
    """Create an unstarted loopback-only server; callers own shutdown/server_close."""
    if host != "127.0.0.1":
        raise ValueError("The inspector must bind to 127.0.0.1.")
    return LocalServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the local Data Quality Inspector browser interface.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535.")
    try:
        server = make_server(port=args.port)
    except OSError as exc:
        print(f"Could not start the local inspector: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Data Quality Inspector: {url}", flush=True)
    if args.open_browser:
        try:
            webbrowser.open(url)
        except (webbrowser.Error, OSError):
            print("Open the address above in your browser to continue.", file=sys.stderr)

    def stop(_signal, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    old_term = signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, old_term)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
