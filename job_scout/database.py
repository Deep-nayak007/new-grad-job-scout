from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    title TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    posted_date TEXT,
                    age_text TEXT NOT NULL DEFAULT '',
                    salary TEXT NOT NULL DEFAULT '',
                    sources TEXT NOT NULL,
                    source_detail TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'Other',
                    grad_2027 INTEGER NOT NULL DEFAULT 0,
                    visa_status TEXT NOT NULL DEFAULT 'Unknown',
                    visa_evidence TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Not applied',
                    saved INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_date DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_visa ON jobs(visa_status);
                CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category);

                CREATE TABLE IF NOT EXISTS refreshes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    discovered_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    source_results TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            db.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._write_lock, self.connect() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            db.commit()

    def start_refresh(self) -> int:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._write_lock, self.connect() as db:
            cursor = db.execute(
                "INSERT INTO refreshes(started_at, status) VALUES(?, 'running')", (now,)
            )
            db.commit()
            return int(cursor.lastrowid)

    def finish_refresh(
        self,
        refresh_id: int,
        status: str,
        discovered_count: int,
        total_count: int,
        source_results: dict[str, Any],
        error: str = "",
    ) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._write_lock, self.connect() as db:
            db.execute(
                """
                UPDATE refreshes
                   SET finished_at = ?, status = ?, discovered_count = ?, total_count = ?,
                       source_results = ?, error = ?
                 WHERE id = ?
                """,
                (now, status, discovered_count, total_count, json.dumps(source_results), error, refresh_id),
            )
            db.commit()

    def latest_refresh(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM refreshes ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            result = dict(row)
            try:
                result["source_results"] = json.loads(result["source_results"])
            except json.JSONDecodeError:
                result["source_results"] = {}
            return result

    def upsert_jobs(self, jobs: Iterable[dict[str, Any]]) -> tuple[int, int]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        new_count = 0
        with self._write_lock, self.connect() as db:
            existing = {row["id"] for row in db.execute("SELECT id FROM jobs")}
            for job in jobs:
                if job["id"] not in existing:
                    new_count += 1
                db.execute(
                    """
                    INSERT INTO jobs (
                        id, company, title, location, url, posted_date, age_text, salary,
                        sources, source_detail, category, grad_2027, visa_status, visa_evidence,
                        first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        company = excluded.company,
                        title = excluded.title,
                        location = excluded.location,
                        url = excluded.url,
                        posted_date = COALESCE(excluded.posted_date, jobs.posted_date),
                        age_text = excluded.age_text,
                        salary = CASE WHEN excluded.salary != '' THEN excluded.salary ELSE jobs.salary END,
                        sources = excluded.sources,
                        source_detail = excluded.source_detail,
                        category = excluded.category,
                        grad_2027 = MAX(jobs.grad_2027, excluded.grad_2027),
                        visa_status = CASE
                            WHEN excluded.visa_status LIKE 'Yes%' THEN excluded.visa_status
                            WHEN jobs.visa_status LIKE 'Yes%' THEN jobs.visa_status
                            WHEN excluded.visa_status LIKE 'Likely%' THEN excluded.visa_status
                            ELSE jobs.visa_status END,
                        visa_evidence = CASE
                            WHEN excluded.visa_evidence != '' THEN excluded.visa_evidence
                            ELSE jobs.visa_evidence END,
                        last_seen = excluded.last_seen
                    """,
                    (
                        job["id"], job["company"], job["title"], job.get("location", ""),
                        job["url"], job.get("posted_date"), job.get("age_text", ""),
                        job.get("salary", ""), json.dumps(job.get("sources", [])),
                        job.get("source_detail", ""), job.get("category", "Other"),
                        int(job.get("grad_2027", False)), job.get("visa_status", "Unknown"),
                        job.get("visa_evidence", ""), now, now,
                    ),
                )
            db.commit()
            total = db.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
        return new_count, int(total)

    def query_jobs(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        clauses: list[str] = []
        params: list[Any] = []
        search = str(filters.get("search", "")).strip()
        if search:
            clauses.append("(company LIKE ? OR title LIKE ? OR location LIKE ?)")
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        category = str(filters.get("category", "")).strip()
        if category and category != "All":
            clauses.append("category = ?")
            params.append(category)
        visa = str(filters.get("visa", "")).strip()
        if visa == "sponsor":
            clauses.append("(visa_status LIKE 'Yes%' OR visa_status LIKE 'Likely%')")
        elif visa == "explicit":
            clauses.append("visa_status LIKE 'Yes%'")
        elif visa == "unknown":
            clauses.append("visa_status = 'Unknown'")
        if str(filters.get("grad_2027", "")).lower() in {"1", "true", "yes"}:
            clauses.append("grad_2027 = 1")
        status = str(filters.get("status", "")).strip()
        if status and status != "All":
            clauses.append("status = ?")
            params.append(status)
        if str(filters.get("saved", "")).lower() in {"1", "true", "yes"}:
            clauses.append("saved = 1")
        days = filters.get("days")
        if days not in (None, "", "all"):
            try:
                clauses.append("posted_date >= date('now', ?)")
                params.append(f"-{max(0, int(days))} days")
            except (TypeError, ValueError):
                pass

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = "SELECT * FROM jobs" + where + " ORDER BY posted_date IS NULL, posted_date DESC, first_seen DESC"
        limit = filters.get("limit")
        if limit:
            sql += " LIMIT ?"
            params.append(min(5000, max(1, int(limit))))
        with self.connect() as db:
            rows = []
            for row in db.execute(sql, params):
                item = dict(row)
                try:
                    item["sources"] = json.loads(item["sources"])
                except json.JSONDecodeError:
                    item["sources"] = [item["sources"]]
                item["grad_2027"] = bool(item["grad_2027"])
                item["saved"] = bool(item["saved"])
                rows.append(item)
            return rows

    def update_job(self, job_id: str, changes: dict[str, Any]) -> bool:
        allowed = {"status", "saved", "notes"}
        assignments = []
        params: list[Any] = []
        for key, value in changes.items():
            if key in allowed:
                assignments.append(f"{key} = ?")
                params.append(int(bool(value)) if key == "saved" else str(value)[:4000])
        if not assignments:
            return False
        params.append(job_id)
        with self._write_lock, self.connect() as db:
            cursor = db.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", params)
            db.commit()
            return cursor.rowcount > 0

    def stats(self) -> dict[str, int]:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN posted_date = date('now', 'localtime') THEN 1 ELSE 0 END) AS today,
                       SUM(grad_2027) AS grad_2027,
                       SUM(CASE WHEN visa_status LIKE 'Yes%' OR visa_status LIKE 'Likely%' THEN 1 ELSE 0 END) AS visa,
                       SUM(CASE WHEN status = 'Applied' THEN 1 ELSE 0 END) AS applied,
                       SUM(saved) AS saved
                  FROM jobs
                """
            ).fetchone()
            return {key: int(row[key] or 0) for key in row.keys()}

