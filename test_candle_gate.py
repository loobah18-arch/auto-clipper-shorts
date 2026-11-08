#!/usr/bin/env python3
"""
Unit tests for Jack Craig Candle Gate module.
"""

import unittest
from pathlib import Path
from candle_gate import CandleGate, VIEW_THRESHOLD_PER_HOUR, SUSTAINED_HOURS_REQUIRED

ROOT_DIR = Path(__file__).resolve().parent


class TestCandleGate(unittest.TestCase):

    def setUp(self):
        self.temp_state = ROOT_DIR / "test_candle_state.json"
        self.gate = CandleGate(self.temp_state)

    def tearDown(self):
        if self.temp_state.exists():
            self.temp_state.unlink()

    def test_active_push_blocked(self):
        # 10 hours of high viral push
        hourly_views = [1000, 5000, 15000, 30000, 80000, 120000, 75000, 50000, 25000, 12000]
        res = self.gate.evaluate_hourly_candles(hourly_views)
        self.assertFalse(res["can_upload"])
        self.assertEqual(res["decision"], "UPLOAD_BLOCKED_ACTIVE_PUSH")
        self.assertEqual(res["current_hourly_rate"], 12000)
        self.assertEqual(res["peak_hourly_rate"], 120000)

    def test_stabilized_drop_cleared(self):
        # Peak push followed by 25 consecutive hours below 2-3k views/hr (e.g. 1,600 views/hr)
        hourly_views = [100000, 50000, 10000] + [1600] * 25
        res = self.gate.evaluate_hourly_candles(hourly_views)
        self.assertTrue(res["can_upload"])
        self.assertEqual(res["decision"], "UPLOAD_CLEARED")
        self.assertGreaterEqual(res["consecutive_hours_below"], SUSTAINED_HOURS_REQUIRED)

    def test_layer2_2k_3k_velocity_blocked(self):
        # Video is currently earning 2,800 views/hour (in 2k-3k range, >= 2,500 threshold) -> blocked
        hourly_views = [500, 1200, 1800, 2400, 2800]
        res = self.gate.evaluate_hourly_candles(hourly_views)
        self.assertFalse(res["can_upload"])
        self.assertEqual(res["decision"], "UPLOAD_BLOCKED_ACTIVE_PUSH")
        self.assertEqual(res["current_hourly_rate"], 2800)

    def test_layer1_early_momentum_blocks_upload(self):
        # Video reaches 2,200 views in 6 hours (>= 2k in 12h) -> Layer 1 must block upload
        res = self.gate.evaluate_early_momentum(total_views=2200, elapsed_hours=6.0)
        self.assertFalse(res["can_upload"])
        self.assertEqual(res["decision"], "UPLOAD_BLOCKED_EARLY_MOMENTUM")
        self.assertTrue(res["has_early_momentum"])

    def test_layer1_low_views_cleared(self):
        # Video has 850 views in 8 hours (< 2k threshold) -> Layer 1 cleared, advances to Layer 2
        res = self.gate.evaluate_early_momentum(total_views=850, elapsed_hours=8.0)
        self.assertTrue(res["can_upload"])
        self.assertEqual(res["decision"], "LAYER_1_CLEARED")
        self.assertFalse(res["has_early_momentum"])

    def test_layer1_past_12h_window_cleared(self):
        # Video reaches 2,500 views after 18 hours (past 12h window) -> Layer 1 cleared
        res = self.gate.evaluate_early_momentum(total_views=2500, elapsed_hours=18.0)
        self.assertTrue(res["can_upload"])
        self.assertEqual(res["decision"], "LAYER_1_CLEARED")

    def test_layer1_near_threshold_sets_1hour_timer(self):
        # Video reaches 1,900 views in 10.5 hours (approaching 2k threshold before 12h) -> 1h timer required
        res = self.gate.evaluate_early_momentum(total_views=1900, elapsed_hours=10.5)
        self.assertFalse(res["can_upload"])
        self.assertTrue(res["requires_timer"])
        self.assertEqual(res["timer_seconds"], 3600)
        self.assertEqual(res["decision"], "RETRY_TIMER_REQUIRED")
        self.assertTrue(res["is_near_threshold"])

    def test_engagement_metrics_pass(self):
        # Jack Craig's metrics from video: 82% STR, 52s AVD on 61s short
        metrics = self.gate.evaluate_engagement_metrics(
            swipe_through_rate=82.0,
            average_view_duration_seconds=52.0,
            video_duration_seconds=61.0,
        )
        self.assertTrue(metrics["str_pass"])
        self.assertTrue(metrics["avd_pass"])
        self.assertTrue(metrics["viral_benchmark_met"])
        self.assertEqual(metrics["status"], "VIRAL_TIER")

    def test_engagement_metrics_fail(self):
        # Low retention video: 60% STR, 25s AVD on 60s short
        metrics = self.gate.evaluate_engagement_metrics(
            swipe_through_rate=60.0,
            average_view_duration_seconds=25.0,
            video_duration_seconds=60.0,
        )
        self.assertFalse(metrics["str_pass"])
        self.assertFalse(metrics["avd_pass"])
        self.assertFalse(metrics["viral_benchmark_met"])
        self.assertEqual(metrics["status"], "BELOW_BENCHMARK")


if __name__ == "__main__":
    unittest.main()
