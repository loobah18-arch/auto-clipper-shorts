#!/usr/bin/env python3
"""
Jack Craig "Tested&Proved" Candle Gate & Upload Cadence Engine.
Reference: https://youtu.be/sKejab-4Rmw ("I BLEW UP a Beginner's YouTube Channel in 7 Days")

Implements the algorithmic rules governing when to upload the next video:
1. The 5,000 Views/Hour Rule: Monitor hourly view candles. Once hourly views drop
   below 5,000 views/hr for at least 24 consecutive hours, upload clearance is granted.
   Rushing to upload while a video is being pushed will flatline the active video.
2. The Swipe-Through Rate (STR) Gate: At least 80% of viewers must make it past the first 3 seconds.
3. Average View Duration (AVD) Gate: At least 80% average view retention (>= 48s for 60s Shorts).
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any, Tuple

DEFAULT_STATE_FILE = Path(__file__).resolve().parent / "candle_gate_state.json"
VIEW_THRESHOLD_PER_HOUR = 5000
SUSTAINED_HOURS_REQUIRED = 24
MIN_SWIPE_THROUGH_RATE = 80.0
MIN_AVD_RATIO = 0.80


class CandleGate:
    """Evaluates YouTube Shorts hourly candles and engagement benchmarks."""

    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self.state_file = state_file

    def evaluate_hourly_candles(
        self,
        hourly_views: List[int],
        threshold: int = VIEW_THRESHOLD_PER_HOUR,
        required_hours: int = SUSTAINED_HOURS_REQUIRED,
    ) -> Dict[str, Any]:
        """
        Evaluates whether the active Short has stabilized below the 5k views/hour threshold.
        """
        if not hourly_views:
            return {
                "decision": "UPLOAD_BLOCKED_NO_DATA",
                "can_upload": False,
                "consecutive_hours_below": 0,
                "current_hourly_rate": 0,
                "peak_hourly_rate": 0,
                "recommendation": "No hourly candle data provided. Wait for initial 24h analytics.",
            }

        peak_rate = max(hourly_views)
        current_rate = hourly_views[-1]

        # Calculate consecutive hours below threshold looking backwards from latest candle
        consecutive_below = 0
        for rate in reversed(hourly_views):
            if rate < threshold:
                consecutive_below += 1
            else:
                break

        can_upload = consecutive_below >= required_hours

        if can_upload:
            decision = "UPLOAD_CLEARED"
            recommendation = (
                f"Active Short has stabilized below {threshold:,} views/hr for {consecutive_below} consecutive hours. "
                f"Cleared to upload next video without cannibalizing active push."
            )
        else:
            decision = "UPLOAD_BLOCKED_ACTIVE_PUSH"
            recommendation = (
                f"Active push in progress! Current rate: {current_rate:,} views/hr (Peak: {peak_rate:,}/hr). "
                f"Only {consecutive_below}/{required_hours} hours below threshold. "
                f"DO NOT upload yet — rushing now will flatline algorithm momentum."
            )

        return {
            "decision": decision,
            "can_upload": can_upload,
            "consecutive_hours_below": consecutive_below,
            "required_hours": required_hours,
            "threshold": threshold,
            "current_hourly_rate": current_rate,
            "peak_hourly_rate": peak_rate,
            "total_candles": len(hourly_views),
            "recommendation": recommendation,
        }

    def evaluate_engagement_metrics(
        self,
        swipe_through_rate: float,
        average_view_duration_seconds: float,
        video_duration_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """
        Scores a Short against Jack Craig's viral benchmarks (80% STR, 80% AVD).
        """
        avd_ratio = average_view_duration_seconds / video_duration_seconds if video_duration_seconds > 0 else 0.0
        avd_percent = avd_ratio * 100.0

        str_pass = swipe_through_rate >= MIN_SWIPE_THROUGH_RATE
        avd_pass = avd_ratio >= MIN_AVD_RATIO
        viral_potential = str_pass and avd_pass

        return {
            "swipe_through_rate": swipe_through_rate,
            "str_pass": str_pass,
            "str_target": MIN_SWIPE_THROUGH_RATE,
            "average_view_duration_seconds": average_view_duration_seconds,
            "video_duration_seconds": video_duration_seconds,
            "avd_percent": round(avd_percent, 1),
            "avd_pass": avd_pass,
            "avd_target_percent": MIN_AVD_RATIO * 100.0,
            "viral_benchmark_met": viral_potential,
            "status": "VIRAL_TIER" if viral_potential else "BELOW_BENCHMARK",
        }

    def save_state(self, state: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def load_state(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}


def main():
    parser = argparse.ArgumentParser(description="Jack Craig Candle Gate & Upload Cadence Evaluator")
    parser.add_argument("--simulate", choices=["active_push", "cleared"], help="Simulate a scenario")
    parser.add_argument("--status", action="store_true", help="Display current candle gate status")
    parser.add_argument("--str", type=float, default=82.0, help="Swipe through rate percentage")
    parser.add_argument("--avd", type=float, default=52.0, help="Average view duration in seconds")
    parser.add_argument("--duration", type=float, default=60.0, help="Video duration in seconds")
    args = parser.parse_args()

    gate = CandleGate()

    if args.simulate == "active_push":
        # Simulate active viral push: high hourly views, last hour at 18k
        sample_candles = [300, 1000, 3000, 4000, 6000, 17000, 45000, 100000, 120000, 80000, 40000, 18000]
        result = gate.evaluate_hourly_candles(sample_candles)
        metrics = gate.evaluate_engagement_metrics(args.str, args.avd, args.duration)
        combined = {"candle_evaluation": result, "engagement_metrics": metrics}
        gate.save_state(combined)
        print(json.dumps(combined, indent=2))
        return

    if args.simulate == "cleared":
        # Simulate post-push stabilization: 26 hours below 5,000 views/hr
        sample_candles = [100000, 80000, 20000] + [2500] * 26
        result = gate.evaluate_hourly_candles(sample_candles)
        metrics = gate.evaluate_engagement_metrics(args.str, args.avd, args.duration)
        combined = {"candle_evaluation": result, "engagement_metrics": metrics}
        gate.save_state(combined)
        print(json.dumps(combined, indent=2))
        return

    # Default status inspect
    saved = gate.load_state()
    if saved:
        print(json.dumps(saved, indent=2))
    else:
        # Default run with sample cleared metrics
        sample = [20000] + [1500] * 25
        result = gate.evaluate_hourly_candles(sample)
        metrics = gate.evaluate_engagement_metrics(args.str, args.avd, args.duration)
        print(json.dumps({"candle_evaluation": result, "engagement_metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
