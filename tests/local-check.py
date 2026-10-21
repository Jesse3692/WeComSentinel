"""Local-only smoke measurements against the development demo server."""
import json
import sqlite3
import statistics
import time
from pathlib import Path

import httpx

from app.config import Settings

s = Settings()
if not s.demo_enabled or s.app_env == "production":
    raise SystemExit("This check is only for the local demo environment")

with httpx.Client(base_url="http://localhost:8000", timeout=10) as client:
    response = client.post("/api/v1/auth/demo", json={"password": s.demo_password, "role": "admin"})
    response.raise_for_status()
    metrics = {}
    for path in ("/api/v1/dashboard", "/api/v1/events?limit=20"):
        samples = []
        for _ in range(30):
            begin = time.perf_counter()
            response = client.get(path)
            response.raise_for_status()
            samples.append((time.perf_counter()-begin)*1000)
        metrics[path] = {"median_ms": round(statistics.median(samples), 2),
                         "p95_ms": round(sorted(samples)[28], 2), "samples": len(samples)}
with sqlite3.connect("data/monitor.db") as db:
    metrics["events"] = db.execute("SELECT COUNT(*) FROM business_events").fetchone()[0]
with sqlite3.connect("backups/initial-demo/monitor.db") as db:
    metrics["backup_integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
    metrics["backup_events"] = db.execute("SELECT COUNT(*) FROM business_events").fetchone()[0]
Path("data/local-check.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(metrics, ensure_ascii=False, indent=2))
