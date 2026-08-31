from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from .exporter import create_workbook
from .sources import fetch_all


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA_DIR = DOCS / "data"


def main() -> None:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    result = fetch_all(config["sources"], config.get("direct_ats"))
    if not result.jobs:
        raise RuntimeError("No jobs were returned; refusing to publish an empty dashboard.")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    jobs = sorted(result.jobs, key=lambda job: (job.get("posted_date") or "", job["company"]), reverse=True)
    for job in jobs:
        job["first_seen"] = now
        job["last_seen"] = now
        job["status"] = "Not applied"
        job["saved"] = False
        job["notes"] = ""

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
        "discovered_count": 0,
        "total_count": len(jobs),
        "source_results": result.source_results,
        "error": "",
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for filename in ("index.html", "styles.css", "app.js"):
        shutil.copy2(ROOT / "static" / filename, DOCS / filename)
    (DOCS / ".nojekyll").touch()
    (DATA_DIR / "jobs.json").write_text(
        json.dumps({"generated_at": now, "stats": stats, "refresh": refresh, "jobs": jobs}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    create_workbook(DATA_DIR / "Job_Scout_New_Grad_2027.xlsx", jobs)
    print(f"Built cloud dashboard with {len(jobs):,} jobs ({stats['today']} posted today).")


if __name__ == "__main__":
    main()

