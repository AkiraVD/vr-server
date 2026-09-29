"""Side-by-side detection and cache-key invalidation."""

import os
import unittest

from .fixture import CLIP, TMP, media


class TestSbsDetection(unittest.TestCase):
    def test_vr_sbs_frame_detected(self):
        """4096x2048 is 2:1 - both eyes in one frame."""
        self.assertTrue(media.is_sbs({"width": 4096, "height": 2048}))

    def test_fisheye_sbs_detected(self):
        self.assertTrue(media.is_sbs({"width": 5800, "height": 2900}))

    def test_flat_video_not_sbs(self):
        self.assertFalse(media.is_sbs({"width": 1920, "height": 1080}))

    def test_missing_metadata_not_sbs(self):
        self.assertFalse(media.is_sbs({}))
        self.assertFalse(media.is_sbs({"width": 0, "height": 0}))

    def test_zero_height_does_not_divide_by_zero(self):
        self.assertFalse(media.is_sbs({"width": 100, "height": 0}))


class TestCacheKey(unittest.TestCase):
    def test_stable_for_unchanged_file(self):
        self.assertEqual(media.cache_key(CLIP), media.cache_key(CLIP))

    def test_missing_file_returns_none(self):
        self.assertIsNone(media.cache_key(os.path.join(TMP, "nope.mp4")))

    def test_changes_when_contents_change(self):
        """A replaced file must not keep serving its old thumbnail."""
        path = os.path.join(TMP, "changing.mp4")
        with open(path, "wb") as fh:
            fh.write(b"first")
        before = media.cache_key(path)
        with open(path, "wb") as fh:
            fh.write(b"second, and longer")
        self.assertNotEqual(before, media.cache_key(path))

    def test_distinct_files_distinct_keys(self):
        other = os.path.join(TMP, "other.mp4")
        with open(other, "wb") as fh:
            fh.write(b"x")
        self.assertNotEqual(media.cache_key(CLIP), media.cache_key(other))


class TestFindVideos(unittest.TestCase):
    def test_finds_video_recursively_and_skips_other_types(self):
        found = media.find_videos([os.path.dirname(CLIP)])
        names = sorted(os.path.basename(p) for p in found)
        self.assertEqual(names, ["clip.mp4", "deep.mkv"])

    def test_no_duplicates_when_scan_entries_nest(self):
        base = os.path.dirname(CLIP)
        found = media.find_videos([base, os.path.join(base, "nested")])
        self.assertEqual(len(found), len(set(found)))


if __name__ == "__main__":
    unittest.main()
