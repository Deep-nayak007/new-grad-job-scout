from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .exporter import create_workbook
from .sources import company_key, fetch_all, listing_title_key, merge_visa, workday_requisition_key


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA_DIR = DOCS / "data"


def valid_snapshot(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("jobs"), list)


def load_previous_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    url = str(config.get("public_snapshot_url", "")).strip()
    if url:
        separator = "&" if "?" in url else "?"
        snapshot_url = f"{url}{separator}previous={int(datetime.now().timestamp())}"
        request = urllib.request.Request(
            snapshot_url,
            headers={"User-Agent": "JobScout/1.0", "Accept": "application/json", "Cache-Control": "no-cache"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                value = json.loads(response.read().decode("utf-8"))
            if valid_snapshot(value):
                return value
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            pass
    local_path = DATA_DIR / "jobs.json"
    try:
        value = json.loads(local_path.read_text(encoding="utf-8"))
        return value if valid_snapshot(value) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def history_signature(job: dict[str, Any]) -> str:
    location = re.sub(r"[^a-z0-9]+", " ", str(job.get("location", "")).lower()).strip()
    return "|".join((company_key(str(job.get("company", ""))), listing_title_key(str(job.get("title", ""))), location))


def apply_snapshot_history(
    jobs: list[dict[str, Any]], previous: dict[str, Any], now: str,
) -> tuple[int, int, set[str]]:
    previous_jobs = [job for job in previous.get("jobs", []) if isinstance(job, dict)]
    previous_generated = str(previous.get("generated_at", ""))
    reset_timestamps = sum(
        str(job.get("first_seen", "")) == previous_generated for job in previous_jobs
    )
    legacy_reset_snapshot = bool(previous_jobs) and reset_timestamps >= len(previous_jobs) * 0.9
    previous_by_id = {str(job.get("id", "")): job for job in previous_jobs if job.get("id")}
    previous_by_requisition = {
        key: job for job in previous_jobs if (key := workday_requisition_key(str(job.get("url", ""))))
    }
    previous_signatures = Counter(history_signature(job) for job in previous_jobs)
    current_signatures = Counter(history_signature(job) for job in jobs)
    unique_previous = {
        history_signature(job): job
        for job in previous_jobs
        if previous_signatures[history_signature(job)] == 1
    }

    discovered_ids: set[str] = set()
    matched_previous_ids: set[str] = set()
    for job in jobs:
        prior = previous_by_id.get(str(job.get("id", "")))
        requisition = workday_requisition_key(str(job.get("url", "")))
        if not prior:
            if requisition:
                prior = previous_by_requisition.get(requisition)
        if not prior and not requisition:
            signature = history_signature(job)
            if current_signatures[signature] == 1:
                prior = unique_previous.get(signature)
        if prior:
            resolved_visa = {
                "visa_status": prior.get("visa_status", "Unknown"),
                "visa_evidence": prior.get("visa_evidence", ""),
                "visa_basis": prior.get("visa_basis", ""),
                "sources": prior.get("sources", []),
            }
            # Current evidence replaces equally authoritative prior evidence,
            # while a transient feed/detail failure cannot erase a stronger
            # previously verified employer statement.
            merge_visa(resolved_visa, job, prefer_incoming_on_equal=True)
            job["visa_status"] = resolved_visa["visa_status"]
            job["visa_evidence"] = resolved_visa["visa_evidence"]
            job["visa_basis"] = resolved_visa["visa_basis"]
            first_seen = str(prior.get("first_seen") or prior.get("last_seen") or now)
            if legacy_reset_snapshot:
                posted_date = str(prior.get("posted_date") or "")
                first_seen = f"{posted_date}T00:00:00+00:00" if posted_date else "1970-01-01T00:00:00+00:00"
            job["first_seen"] = first_seen
            if prior.get("id"):
                matched_previous_ids.add(str(prior["id"]))
        else:
            job["first_seen"] = now
            discovered_ids.add(job["id"])
        job["last_seen"] = now
        job["status"] = "Not applied"
        job["saved"] = False
        job["notes"] = ""

    removed_count = len(set(previous_by_id) - matched_previous_ids)
    return len(discovered_ids), removed_count, discovered_ids


def validate_build(
    jobs: list[dict[str, Any]], previous: dict[str, Any], source_results: dict[str, dict[str, Any]], source_names: list[str],
) -> None:
    if not jobs:
        raise RuntimeError("No jobs were returned; refusing to publish an empty dashboard.")
    healthy_core = sum(source_results.get(name, {}).get("status") == "ok" for name in source_names)
    minimum_healthy = max(1, (len(source_names) + 1) // 2)
    if healthy_core < minimum_healthy:
        raise RuntimeError(f"Only {healthy_core} of {len(source_names)} core feeds were healthy; keeping the prior site.")
    previous_count = len(previous.get("jobs", [])) if valid_snapshot(previous) else 0
    if previous_count >= 100 and len(jobs) < previous_count * 0.55:
        raise RuntimeError(
            f"Job count fell from {previous_count:,} to {len(jobs):,}; refusing to publish a likely partial build."
        )


def main() -> None:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    previous = load_previous_snapshot(config)
    result = fetch_all(config["sources"], config.get("direct_ats"))
    validate_build(result.jobs, previous, result.source_results, [source["name"] for source in config["sources"]])

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    jobs = result.jobs
    discovered_count, removed_count, discovered_ids = apply_snapshot_history(jobs, previous, now)
    jobs.sort(
        key=lambda job: (job["id"] in discovered_ids, job.get("posted_date") or "", job["company"]),
        reverse=True,
    )

    today = date.today().isoformat()
    stats = {
        "total": len(jobs),
        "today": sum(job.get("posted_date") == today for job in jobs),
        "grad_2027": sum(bool(job.get("grad_2027")) for job in jobs),
        "visa": sum(job["visa_status"].startswith(("Yes", "Likely")) for job in jobs),
        "applied": 0,
        "saved": 0,
    }
    refresh = {
        "id": int(datetime.now().timestamp()),
        "started_at": now,
        "finished_at": now,
        "status": "completed",
        "discovered_count": discovered_count,
        "removed_count": removed_count,
        "new_job_ids": sorted(discovered_ids),
        "total_count": len(jobs),
        "source_results": result.source_results,
        "error": "",
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for filename in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ROOT / "static" / filename, DOCS / filename)
    (DOCS / ".nojekyll").touch()
    snapshot = {"generated_at": now, "stats": stats, "refresh": refresh, "jobs": jobs}
    (DATA_DIR / "jobs.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    meta_refresh = {key: value for key, value in refresh.items() if key != "new_job_ids"}
    (DATA_DIR / "meta.json").write_text(
        json.dumps({"generated_at": now, "stats": stats, "refresh": meta_refresh}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    create_workbook(DATA_DIR / "Job_Scout_New_Grad_2027.xlsx", jobs)
    print(
        f"Built cloud dashboard with {len(jobs):,} jobs "
        f"({discovered_count} new, {removed_count} removed, {stats['today']} posted today)."
    )


if __name__ == "__main__":
    main()
