"""Facecam selection: stable face across samples, overlay vs fullscreen, locked crop, border snapping."""
import unittest

from ccf import facecam, render

W, H = 1920, 1080


def cam_face(i):
    # Kai Cenat-style overlay face (mid-left), tiny jitter between samples
    return (148 + (i % 3) - 1, 518 + (i % 2), 57, 74, 0.92)


class SelectFace(unittest.TestCase):
    def test_stable_overlay_face_mid_left_is_found(self):
        # The old detector only looked in the corners: a cam at mid-height on the left was missed.
        faces = [[cam_face(i)] for i in range(10)]
        sel = facecam.select_face(faces, W, H)
        self.assertIsNotNone(sel)
        self.assertEqual(sel["kind"], "overlay")
        self.assertEqual(sel["hits"], 10)
        x, y, w, h = sel["box"]
        self.assertAlmostEqual(x, 148, delta=2)
        self.assertAlmostEqual(y, 518, delta=2)

    def test_face_in_30_percent_of_samples_is_enough(self):
        faces = [[cam_face(i)] if i in (1, 4, 8) else [] for i in range(10)]
        self.assertIsNotNone(facecam.select_face(faces, W, H))

    def test_face_in_too_few_samples_is_rejected(self):
        faces = [[cam_face(i)] if i in (1, 4) else [] for i in range(10)]
        self.assertIsNone(facecam.select_face(faces, W, H))

    def test_moving_game_character_does_not_win(self):
        # A cutscene face drifting across the screen never forms a stable cluster
        faces = [[(200 + i * 150, 300 + i * 30, 90, 110, 0.9)] for i in range(10)]
        self.assertIsNone(facecam.select_face(faces, W, H))

    def test_overlay_beats_cutscene_closeup(self):
        faces = [[cam_face(i), (700, 200, 420, 500, 0.95)] for i in range(10)]
        sel = facecam.select_face(faces, W, H)
        self.assertEqual(sel["kind"], "overlay")
        self.assertAlmostEqual(sel["box"][0], 148, delta=2)

    def test_implausible_sizes_ignored(self):
        tiny = [[(100, 100, 20, 24, 0.9)] for _ in range(10)]          # ~1% of width: a crowd face
        huge = [[(300, 0, 900, 1000, 0.9)] for _ in range(10)]         # 47% of width
        self.assertIsNone(facecam.select_face(tiny, W, H))
        self.assertIsNone(facecam.select_face(huge, W, H))

    def test_big_stable_face_is_fullscreen(self):
        faces = [[(860 + i % 2, 260, 300, 380, 0.95)] for i in range(10)]
        sel = facecam.select_face(faces, W, H)
        self.assertEqual(sel["kind"], "fullscreen")

    def test_median_box_ignores_outlier_sample(self):
        faces = [[cam_face(i)] for i in range(9)] + [[(182, 531, 61, 79, 0.9)]]
        x, y, w, h = facecam.select_face(faces, W, H)["box"]
        self.assertLess(abs(x - 148), 3)


class Boxes(unittest.TestCase):
    def test_fullscreen_box_is_9x16_on_face_and_clamped(self):
        x, y, w, h = facecam.fullscreen_box((860, 260, 300, 380), W, H)
        self.assertEqual((y, h), (0, H))
        self.assertAlmostEqual(w / h, 9 / 16, delta=0.01)
        self.assertLessEqual(x, 860)
        self.assertGreaterEqual(x + w, 1160)
        x2, _, w2, _ = facecam.fullscreen_box((1800, 260, 100, 120), W, H)
        self.assertLessEqual(x2 + w2, W)

    def test_padded_box_inside_frame_and_even(self):
        for face in [(0, 0, 60, 70), (1850, 1000, 60, 70), (148, 518, 57, 74)]:
            x, y, w, h = facecam.padded_cam_box(face, W, H)
            self.assertTrue(0 <= x and 0 <= y and x + w <= W and y + h <= H)
            self.assertTrue(all(v % 2 == 0 for v in (x, y, w, h)))

    def test_rect_from_sides_rejects_implausible(self):
        face = (148, 518, 57, 74)
        ok = facecam.rect_from_sides({"left": 0, "right": 388, "top": 432, "bottom": 654}, face, W, H)
        self.assertEqual(ok, (0, 432, 388, 222))
        # whole screen = not an overlay border
        self.assertIsNone(facecam.rect_from_sides({"left": 0, "right": W, "top": 0, "bottom": H}, face, W, H))
        # missing side
        self.assertIsNone(facecam.rect_from_sides({"left": 0, "right": 388, "top": None, "bottom": 654}, face, W, H))

    def test_snap_by_motion_finds_overlay_border(self):
        import numpy as np
        # 640x360 motion map: busy gameplay everywhere, quiet cam box at x<130, 144<=y<218 (1/3 scale)
        m = np.full((360, 640), 40.0, np.float32)
        m[144:218, 0:130] = 4.0
        m[170:200, 40:80] = 30.0           # the streamer moving inside the cam
        sides = facecam.snap_by_motion(m, (148, 518, 57, 74), W, H)
        rect = facecam.rect_from_sides(sides, (148, 518, 57, 74), W, H)
        self.assertIsNotNone(rect)
        x, y, w, h = rect
        self.assertEqual(x, 0)
        self.assertAlmostEqual(x + w, 390, delta=12)
        self.assertAlmostEqual(y, 432, delta=12)
        self.assertAlmostEqual(y + h, 654, delta=12)


class Layout(unittest.TestCase):
    def test_choose_layout_uses_given_cam_info(self):
        info = {"kind": "overlay", "box": (0, 434, 374, 206)}
        self.assertEqual(render.choose_layout("stack", "auto", None, W, H, cam_info=info),
                         ("stack", (0, 434, 374, 206)))
        info = {"kind": "fullscreen", "box": (706, 0, 608, 1080)}
        self.assertEqual(render.choose_layout("stack", "auto", None, W, H, cam_info=info),
                         ("facecrop", (706, 0, 608, 1080)))

    def test_facecrop_graph_is_single_locked_panel(self):
        g, cap_y, _ = render.video_graph("facecrop", W, H, cam=(706, 0, 608, 1080))
        self.assertIn("crop=608:1080:706:0", g)
        self.assertNotIn("boxblur", g)
        self.assertNotIn("vstack", g)

    def test_stack_gameplay_panel_is_sharp_centre_crop(self):
        g, _, _ = render.video_graph("stack", W, H, cam=(0, 434, 374, 206))
        game = g.split("[cam];")[1]
        self.assertNotIn("boxblur", g)                      # no blurred fill used as the gameplay panel
        self.assertIn("crop=956:1080:482:0", game)          # centred, full height
        self.assertIn("scale=1080:1220", game)

    def test_gameplay_crop_is_always_frame_centre(self):
        for cam in (None, (900, 300, 700, 700), (200, 300, 600, 700), (0, 434, 374, 206), (1500, 0, 420, 300)):
            gx = render.gameplay_crop_x(W, 956, cam)
            self.assertEqual(gx, 482)                       # (1920-956)/2, ignores the cam
            self.assertEqual(gx + 956 / 2, W / 2)           # horizontally centred on the frame centre
        self.assertEqual(render.gameplay_crop_x(1280, 628), 326)
        self.assertEqual(render.gameplay_crop_x(800, 956), 0)   # narrower source: no negative offset

    def test_stack_gameplay_panel_full_height_centred_with_big_cam(self):
        g, _, _ = render.video_graph("stack", W, H, cam=(900, 300, 700, 700))
        self.assertIn("crop=956:1080:482:0", g.split("[cam];")[1])   # full height => vertically centred


if __name__ == "__main__":
    unittest.main()
