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

# Layer 1 Constants: Early Momentum Gate & Near-Threshold Recheck Timer
EARLY_HOURS_WINDOW = 12.0
EARLY_VIEWS_THRESHOLD = 2000
NEAR_THRESHOLD_PERCENT = 0.85  # 85% of threshold (e.g. >= 1,700 views when threshold is 2,000)
NEAR_THRESHOLD_VIEWS = int(EARLY_VIEWS_THRESHOLD * NEAR_THRESHOLD_PERCENT)
TIMER_DELAY_SECONDS = 3600     # 1 hour recheck timer


class CandleGate:
    """Evaluates YouTube Shorts hourly candles and multi-layer engagement benchmarks."""

    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self.state_file = state_file

    def evaluate_early_momentum(
        self,
        total_views: int,
        elapsed_hours: float,
        threshold_views: int = EARLY_VIEWS_THRESHOLD,
        window_hours: float = EARLY_HOURS_WINDOW,
        near_threshold_views: int = NEAR_THRESHOLD_VIEWS,
        timer_seconds: int = TIMER_DELAY_SECONDS,
    ) -> Dict[str, Any]:
        """
        Layer 1 - Early Momentum Gate:
        Checks if the video reaches >= 2,000 views within its first 12 hours.
        Also detects if a video is approaching 2k (e.g. 1.9k) before 12 hours,
        triggering an automatic 1-hour timer to re-evaluate before deciding to upload.
        """
        is_early_window = elapsed_hours <= window_hours
        has_early_momentum = is_early_window and total_views >= threshold_views
        is_near_threshold = is_early_window and (near_threshold_views <= total_views < threshold_views)

        if has_early_momentum:
            decision = "UPLOAD_BLOCKED_EARLY_MOMENTUM"
            can_upload = False
            requires_timer = False
            recommendation = (
                f"🚀 Layer 1 Triggered: Early momentum detected! Video has reached {total_views:,} views "
                f"in {elapsed_hours:.1f}h (>= {threshold_views:,} views in {window_hours:.0f}h). "
                f"Pausing upload to let early algorithmic traction perform before moving to Layer 2."
            )
        elif is_near_threshold:
            decision = "RETRY_TIMER_REQUIRED"
            can_upload = False
            requires_timer = True
            remaining_window = max(window_hours - elapsed_hours, 0.0)
            recommendation = (
                f"⏳ Video is at {total_views:,} views in {elapsed_hours:.1f}h (approaching {threshold_views:,} threshold "
                f"with {remaining_window:.1f}h remaining in the 12h window). "
                f"Automatically setting a 1-hour timer ({timer_seconds // 60}m) to re-check if it crosses 2k before uploading."
            )
        else:
            decision = "LAYER_1_CLEARED"
            can_upload = True
            requires_timer = False
            if is_early_window:
                recommendation = (
                    f"Video has {total_views:,} views in {elapsed_hours:.1f}h (< {threshold_views:,} threshold). "
                    f"Layer 1 cleared; evaluating under Layer 2."
                )
            else:
                recommendation = (
                    f"Video is past the initial {window_hours:.0f}h window ({elapsed_hours:.1f}h elapsed). "
                    f"Layer 1 passed; evaluating under Layer 2."
                )

        return {
            "layer": 1,
            "decision": decision,
            "can_upload": can_upload,
            "requires_timer": requires_timer,
            "timer_seconds": timer_seconds,
            "total_views": total_views,
            "elapsed_hours": round(elapsed_hours, 2),
            "threshold_views": threshold_views,
            "window_hours": window_hours,
            "is_early_window": is_early_window,
            "has_early_momentum": has_early_momentum,
            "is_near_threshold": is_near_threshold,
            "recommendation": recommendation,
        }

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

    def evaluate_live_channel(self, youtube_client=None) -> Dict[str, Any]:
        """
        Polls YouTube Data API v3 for the channel's most recent video performance.
        Computes velocity and blocks uploads if an active push is in progress.
        """
        if youtube_client is None:
            return self.load_state()

        try:
            from datetime import datetime, timezone

            channels_response = youtube_client.channels().list(mine=True, part="contentDetails").execute()
            items = channels_response.get("items", [])
            if not items:
                return {"can_upload": True, "reason": "No channel found; initial upload cleared"}

            uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            playlist_items = youtube_client.playlistItems().list(
                playlistId=uploads_id, part="snippet", maxResults=1
            ).execute()
            video_items = playlist_items.get("items", [])
            if not video_items:
                return {"can_upload": True, "reason": "No prior uploads found; initial upload cleared"}

            vid_id = video_items[0]["snippet"]["resourceId"]["videoId"]
            title = video_items[0]["snippet"]["title"]
            published_at_str = video_items[0]["snippet"]["publishedAt"]

            vid_stats = youtube_client.videos().list(id=vid_id, part="statistics").execute()
            v_items = vid_stats.get("items", [])
            if not v_items:
                return {"can_upload": True, "reason": "Could not fetch video stats"}

            total_views = int(v_items[0]["statistics"].get("viewCount", 0))
            published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed_hours = max((now - published_at).total_seconds() / 3600.0, 0.1)

            # Load previous snapshot from state
            state = self.load_state()
            history_candles = state.get("hourly_candles", [])
            last_snapshot = state.get("last_snapshot", {})

            # Calculate hourly delta
            if last_snapshot.get("video_id") == vid_id and "timestamp" in last_snapshot:
                prev_views = last_snapshot.get("total_views", 0)
                try:
                    prev_time = datetime.fromisoformat(last_snapshot["timestamp"])
                    delta_hours = max((now - prev_time).total_seconds() / 3600.0, 0.1)
                    hourly_velocity = max(int((total_views - prev_views) / delta_hours), 0)
                except Exception:
                    hourly_velocity = int(total_views / elapsed_hours)
            else:
                hourly_velocity = int(total_views / elapsed_hours)

            history_candles.append(hourly_velocity)
            history_candles = history_candles[-48:]

            # Layer 1: Early Momentum Gate (>= 2,000 views within 12 hours)
            layer1_eval = self.evaluate_early_momentum(total_views, elapsed_hours)

            # Layer 2: 5,000 views/hour sustained velocity candle monitoring
            layer2_eval = self.evaluate_hourly_candles(history_candles)
            if elapsed_hours <= 48 and hourly_velocity >= VIEW_THRESHOLD_PER_HOUR:
                layer2_eval["can_upload"] = False
                layer2_eval["decision"] = "UPLOAD_BLOCKED_ACTIVE_PUSH"
                layer2_eval["recommendation"] = (
                    f"🔥 Active Short '{title}' is performing well ({hourly_velocity:,} views/hr)! "
                    f"Letting it perform on the algorithm before uploading another."
                )

            requires_timer = layer1_eval.get("requires_timer", False)
            timer_seconds = layer1_eval.get("timer_seconds", 3600)

            # Precedence: Layer 1 halts early momentum, then Layer 2 evaluates viral velocity
            if not layer1_eval["can_upload"]:
                final_decision = layer1_eval["decision"]
                can_upload = False
                final_recommendation = layer1_eval["recommendation"]
                active_layer = "Layer 1 (Early Momentum: 1-Hour Timer)" if requires_timer else "Layer 1 (Early Momentum Gate: >=2k views in 12h)"
            elif not layer2_eval["can_upload"]:
                final_decision = layer2_eval["decision"]
                can_upload = False
                final_recommendation = layer2_eval["recommendation"]
                active_layer = "Layer 2 (Viral Velocity Gate: >=5k views/hr)"
            else:
                final_decision = "UPLOAD_CLEARED"
                can_upload = True
                final_recommendation = layer2_eval["recommendation"]
                active_layer = "All Layers Cleared"

            candle_eval = {
                "decision": final_decision,
                "can_upload": can_upload,
                "requires_timer": requires_timer,
                "timer_seconds": timer_seconds,
                "active_layer": active_layer,
                "recommendation": final_recommendation,
                "layer1": layer1_eval,
                "layer2": layer2_eval,
            }

            updated_state = {
                "candle_evaluation": candle_eval,
                "hourly_candles": history_candles,
                "last_snapshot": {
                    "video_id": vid_id,
                    "title": title,
                    "total_views": total_views,
                    "timestamp": now.isoformat(),
                    "hourly_velocity": hourly_velocity,
                    "elapsed_hours": round(elapsed_hours, 2),
                }
            }
            self.save_state(updated_state)
            return updated_state

        except Exception as e:
            return {"can_upload": True, "reason": f"Live query fallback ({e}); clearance granted"}


def main():
    parser = argparse.ArgumentParser(description="Jack Craig Candle Gate & Upload Cadence Evaluator")
    parser.add_argument("--simulate", choices=["near_threshold", "early_momentum", "active_push", "cleared"], help="Simulate a scenario")
    parser.add_argument("--status", action="store_true", help="Display current candle gate status")
    parser.add_argument("--str", type=float, default=82.0, help="Swipe through rate percentage")
    parser.add_argument("--avd", type=float, default=52.0, help="Average view duration in seconds")
    parser.add_argument("--duration", type=float, default=60.0, help="Video duration in seconds")
    args = parser.parse_args()

    gate = CandleGate()

    if args.simulate == "near_threshold":
        # Simulate video at 1,900 views in 10.5 hours (approaching 2k before 12 hours)
        total_views = 1900
        elapsed_hours = 10.5
        layer1 = gate.evaluate_early_momentum(total_views, elapsed_hours)
        layer2 = gate.evaluate_hourly_candles([100, 150, 250, 350, 500])
        combined = {
            "candle_evaluation": {
                "decision": layer1["decision"],
                "can_upload": layer1["can_upload"],
                "requires_timer": layer1["requires_timer"],
                "timer_seconds": layer1["timer_seconds"],
                "active_layer": "Layer 1 (Early Momentum: 1-Hour Timer)",
                "recommendation": layer1["recommendation"],
                "layer1": layer1,
                "layer2": layer2,
            },
            "last_snapshot": {
                "total_views": total_views,
                "elapsed_hours": elapsed_hours,
            }
        }
        gate.save_state(combined)
        print(json.dumps(combined, indent=2))
        return

    if args.simulate == "early_momentum":
        # Simulate Layer 1 early momentum: video reaches 2,500 views in 7.0 hours (<12h)
        total_views = 2500
        elapsed_hours = 7.0
        layer1 = gate.evaluate_early_momentum(total_views, elapsed_hours)
        layer2 = gate.evaluate_hourly_candles([100, 200, 350, 450, 600, 800])
        combined = {
            "candle_evaluation": {
                "decision": layer1["decision"],
                "can_upload": layer1["can_upload"],
                "active_layer": "Layer 1 (Early Momentum Gate: >=2k views in 12h)",
                "recommendation": layer1["recommendation"],
                "layer1": layer1,
                "layer2": layer2,
            },
            "last_snapshot": {
                "total_views": total_views,
                "elapsed_hours": elapsed_hours,
            }
        }
        gate.save_state(combined)
        print(json.dumps(combined, indent=2))
        return

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
