#!/usr/bin/env python3
"""
24-Hour Spacing Pre-flight Gate (Layer 0 front-runner for the Hypit pipeline).

Reuses candle_gate.CandleGate.evaluate_upload_spacing() so the workflow can
skip the entire expensive pipeline (tests, TTS, PO token, 5-tier download,
render) when fewer than 24 hours have elapsed since the last successful
YouTube upload — instead of discovering that at the upload step.

Decision matrix (fail-open by design, mirroring candle_gate.py):
  - no history / no valid entries / parse error  -> proceed (fail-open)
  - elapsed >= 24h                               -> proceed
  - remaining <= 30 min                          -> inline sleep, then proceed
  - otherwise                                    -> skip pipeline (proceed=false)

Manual workflow_dispatch runs set PREFLIGHT_BYPASS to always proceed.
Exit code is always 0; the decision is carried via the `proceed` GitHub
Actions output so downstream `if:` conditions stay simple.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from candle_gate import CandleGate

DEFAULT_HISTORY = Path(__file__).resolve().parent / "upload_history.json"
DEFAULT_MIN_SPACING_HOURS = 24.0
DEFAULT_MAX_INLINE_WAIT_HOURS = 0.5
HARD_SLEEP_CAP_SECONDS = 1900  # defensive: below the preflight job's 45-min timeout


def _parse_timestamp(raw: str) -> datetime:
    ts = raw.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _last_valid_upload(records):
    """Latest entry representing a real successful upload. Skips dry-run ids,
    non-uploaded statuses, and entries without a usable timestamp (fail-open
    direction: a stricter filter errs toward re-running, and the upload step's
    candle gate still enforces Layer 0 anyway)."""
    for rec in reversed(records if isinstance(records, list) else []):
        if not isinstance(rec, dict):
            continue
        vid = str(rec.get("video_id") or "")
        if not vid or vid.startswith("dry_run"):
            continue
        if rec.get("status") != "uploaded":
            continue
        raw_ts = rec.get("timestamp")
        if not raw_ts or not isinstance(raw_ts, str):
            continue
        return rec, raw_ts
    return None, None


def decide(
    history_path=DEFAULT_HISTORY,
    now=None,
    min_spacing_hours: float = DEFAULT_MIN_SPACING_HOURS,
    max_inline_wait_hours: float = DEFAULT_MAX_INLINE_WAIT_HOURS,
) -> dict:
    """Pure decision function (no sleeping, no side effects) for testability."""
    now = now or datetime.now(timezone.utc)

    try:
        history = Path(history_path)
        records = json.loads(history.read_text(encoding="utf-8")) if history.exists() else []
    except Exception as exc:
        return {"proceed": True, "sleep_seconds": 0, "elapsed_hours": None,
                "last_video_id": None, "reason": f"fail-open: unreadable history ({exc})"}

    rec, raw_ts = _last_valid_upload(records)
    if rec is None:
        return {"proceed": True, "sleep_seconds": 0, "elapsed_hours": None,
                "last_video_id": None, "reason": "no prior successful upload found; pipeline cleared"}

    try:
        published_at = _parse_timestamp(raw_ts)
    except Exception as exc:
        return {"proceed": True, "sleep_seconds": 0, "elapsed_hours": None,
                "last_video_id": rec.get("video_id"),
                "reason": f"fail-open: unparseable timestamp {raw_ts!r} ({exc})"}

    elapsed_hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
    spacing = CandleGate().evaluate_upload_spacing(
        elapsed_hours, min_spacing_hours, max_inline_wait_hours
    )

    base = {"elapsed_hours": round(elapsed_hours, 2), "last_video_id": rec.get("video_id")}
    if spacing["can_upload"]:
        return {**base, "proceed": True, "sleep_seconds": 0, "reason": spacing["recommendation"]}
    if spacing["requires_spacing_sleep"]:
        return {**base, "proceed": True,
                "sleep_seconds": min(spacing["spacing_sleep_seconds"], HARD_SLEEP_CAP_SECONDS),
                "reason": spacing["recommendation"]}
    return {**base, "proceed": False, "sleep_seconds": 0, "reason": spacing["recommendation"]}


def main() -> int:
    bypass = (
        os.environ.get("PREFLIGHT_BYPASS", "").strip().lower() in {"1", "true", "yes"}
        or "--bypass" in sys.argv
    )

    if bypass:
        result = {"proceed": True, "sleep_seconds": 0, "elapsed_hours": None,
                  "last_video_id": None,
                  "reason": "PREFLIGHT_BYPASS set (manual dispatch) — spacing gate bypassed"}
    else:
        try:
            result = decide()
        except Exception as exc:  # belt-and-braces fail-open
            result = {"proceed": True, "sleep_seconds": 0, "elapsed_hours": None,
                      "last_video_id": None, "reason": f"fail-open: unexpected gate error ({exc})"}

    if result["proceed"] and result["sleep_seconds"] > 0:
        print(f"⏱️ Inline spacing sleep: {result['sleep_seconds'] // 60}m "
              f"{result['sleep_seconds'] % 60}s (within "
              f"{DEFAULT_MAX_INLINE_WAIT_HOURS * 60:.0f}-min inline window)")
        time.sleep(result["sleep_seconds"])

    icon = "✅ PROCEED" if result["proceed"] else "🛑 SKIP"
    print(f"{icon} | elapsed={result['elapsed_hours']}h | last_upload={result['last_video_id']}")
    print(result["reason"])

    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"proceed={'true' if result['proceed'] else 'false'}\n")
            f.write(f"sleep_seconds={result['sleep_seconds']}\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 🕯️ 24-Hour Spacing Pre-flight\n\n"
                    f"{icon} — elapsed **{result['elapsed_hours']}h** since last upload "
                    f"(`{result['last_video_id']}`)\n\n> {result['reason']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
