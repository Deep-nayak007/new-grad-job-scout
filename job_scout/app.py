from __future__ import annotations

import argparse
import fcntl
import json
import mimetypes
import os
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .database import Database
from .exporter import create_workbook
from .sources import fetch_all


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
EXPORTS = ROOT / "exports"
CONFIG_PATH = ROOT / "config.json"
DB = Database(DATA / "job_scout.db")


class RefreshManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_result: dict[str, Any] = {}
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def start(self, reason: str = "manual") -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.thread = threading.Thread(target=self._run, args=(reason,), name="job-refresh", daemon=True)
            self.thread.start()
            return True

    def _run(self, reason: str) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        lock_handle = (DATA / "refresh.lock").open("a+")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            self.last_result = {"status": "skipped", "reason": "Another Job Scout process is already refreshing."}
            with self.lock:
                self.running = False
            return
        refresh_id = DB.start_refresh()
        baseline_exists = DB.get_setting("baseline_complete", "0") == "1"
        try:
            result = fetch_all(self.config["sources"], self.config.get("direct_ats"))
            if not result.jobs and all(item.get("status") == "error" for item in result.source_results.values()):
                raise RuntimeError("Every source failed; existing jobs were kept unchanged.")
            raw_new_count, total = DB.upsert_jobs(result.jobs)
            discovered = raw_new_count if baseline_exists else 0
            DB.set_setting("baseline_complete", "1")
            DB.set_setting("last_successful_refresh", datetime.now().astimezone().isoformat(timespec="seconds"))
            workbook = create_workbook(EXPORTS / "Job_Scout_New_Grad_2027.xlsx", DB.query_jobs())
            DB.finish_refresh(refresh_id, "completed", discovered, total, result.source_results)
            self.last_result = {
                "id": refresh_id,
                "status": "completed",
                "reason": reason,
                "discovered_count": discovered,
                "total_count": total,
                "workbook": str(workbook),
            }
            if discovered:
                self._desktop_notification(discovered)
        except Exception as exc:  # keep the local service available even if a remote feed changes
            DB.finish_refresh(refresh_id, "failed", 0, DB.stats()["total"], {}, str(exc))
            self.last_result = {"id": refresh_id, "status": "failed", "error": str(exc)}
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
            with self.lock:
                self.running = False

    @staticmethod
    def _desktop_notification(count: int) -> None:
        if os.uname().sysname != "Darwin":
            return
        message = f"{count} new matching job{'s' if count != 1 else ''} found."
        script = 'display notification "' + message.replace('"', '\\"') + '" with title "Job Scout" subtitle "New graduate roles"'
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=5, capture_output=True)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def status(self) -> dict[str, Any]:
        latest = DB.latest_refresh()
        return {"running": self.running, "latest": latest, "last_result": self.last_result}


REFRESH = RefreshManager()


class JobScoutHandler(BaseHTTPRequestHandler):
    server_version = "JobScout/1.0"

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("JOB_SCOUT_VERBOSE") == "1":
            super().log_message(fmt, *args)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        if path == "/api/jobs":
            jobs = DB.query_jobs(query)
            self._json({"jobs": jobs, "count": len(jobs)})
            return
        if path == "/api/stats":
            self._json({"stats": DB.stats(), "refresh": REFRESH.status()})
            return
        if path == "/api/refresh/status":
            self._json(REFRESH.status())
            return
        if path == "/api/sources":
            latest = DB.latest_refresh() or {}
            source_results = latest.get("source_results", {})
            sources = []
            for item in REFRESH.config["sources"]:
                sources.append({**item, "url": item.get("homepage", item["url"]), "result": source_results.get(item["name"], {})})
            self._json({"sources": sources})
            return
        if path == "/api/export":
            target = create_workbook(EXPORTS / "Job_Scout_New_Grad_2027.xlsx", DB.query_jobs())
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="Job_Scout_New_Grad_2027.xlsx"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        if self.path == "/api/refresh":
            started = REFRESH.start("manual")
            self._json({"started": started, "message": "Refresh started" if started else "A refresh is already running"}, 202)
            return
        self._json({"error": "Not found"}, 404)

    def do_PATCH(self) -> None:
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            updated = DB.update_job(job_id, self._read_json())
            self._json({"updated": updated}, 200 if updated else 404)
            return
        self._json({"error": "Not found"}, 404)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        requested = (STATIC / relative).resolve()
        if STATIC.resolve() not in requested.parents and requested != STATIC.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not requested.is_file():
            requested = STATIC / "index.html"
        body = requested.read_bytes()
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def scheduler_loop(refresh_hour: int) -> None:
    while True:
        now = datetime.now().astimezone()
        last = DB.get_setting("last_successful_refresh", "") or ""
        last_date = None
        try:
            last_date = datetime.fromisoformat(last).astimezone().date()
        except ValueError:
            pass
        if (last_date is None or last_date < now.date()) and now.hour >= refresh_hour:
            REFRESH.start("daily schedule")
        time.sleep(60)


def should_refresh_on_startup() -> bool:
    last = DB.get_setting("last_successful_refresh", "") or ""
    try:
        return datetime.now().astimezone() - datetime.fromisoformat(last).astimezone() > timedelta(hours=6)
    except ValueError:
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Job Scout web application.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--refresh-only", action="store_true")
    args = parser.parse_args()

    if args.refresh_only:
        REFRESH.start("scheduled refresh")
        while REFRESH.running:
            time.sleep(0.2)
        raise SystemExit(0 if REFRESH.last_result.get("status") in {"completed", "skipped"} else 1)

    refresh_hour = int(REFRESH.config.get("refresh_hour_local", 8))
    threading.Thread(target=scheduler_loop, args=(refresh_hour,), name="daily-scheduler", daemon=True).start()
    if should_refresh_on_startup():
        REFRESH.start("startup")
    server = ThreadingHTTPServer((args.host, args.port), JobScoutHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Job Scout is running at {url}")
    print("Leave this process running for daily refreshes and notifications. Press Control-C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJob Scout stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
