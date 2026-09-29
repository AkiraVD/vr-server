"""What the directory page shows, and how it formats things."""

import unittest

from .fixture import MEDIA, ROOT, listing


class TestHumanSize(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(listing.human_size(512), "512 B")

    def test_kilobytes(self):
        self.assertEqual(listing.human_size(2048), "2 KB")

    def test_gigabytes(self):
        self.assertEqual(listing.human_size(8 * 1024 ** 3), "8.0 GB")

    def test_zero(self):
        self.assertEqual(listing.human_size(0), "0 B")


class TestHumanTime(unittest.TestCase):
    def test_under_an_hour(self):
        self.assertEqual(listing.human_time(125), "2:05")

    def test_over_an_hour(self):
        self.assertEqual(listing.human_time(3661), "1:01:01")

    def test_unknown_duration_is_blank(self):
        self.assertEqual(listing.human_time(0), "")
        self.assertEqual(listing.human_time(None), "")


class TestVisibleNames(unittest.TestCase):
    def test_hides_unplayable_files(self):
        names = listing.visible_names(MEDIA, at_root=False)
        self.assertIn("clip.mp4", names)
        self.assertIn("nested", names)
        self.assertNotIn("notes.txt", names)
        self.assertNotIn("clip.srt", names)

    def test_root_shows_only_configured_folders(self):
        names = listing.visible_names(ROOT, at_root=True)
        self.assertIn("media", names)
        self.assertNotIn("outside", names)

    def test_folders_sort_before_files(self):
        names = listing.visible_names(MEDIA, at_root=False)
        self.assertEqual(names[0], "nested")


class TestRender(unittest.TestCase):
    def test_page_lists_the_video_and_omits_the_rest(self):
        page = listing.render(MEDIA)
        self.assertIn("clip.mp4", page)
        self.assertNotIn("notes.txt", page)
        self.assertNotIn("clip.srt", page)

    def test_thumbnail_url_present(self):
        self.assertIn("/_thumb/", listing.render(MEDIA))

    def test_escapes_html_in_names(self):
        import os
        tricky = os.path.join(MEDIA, "a<script>.mp4")
        open(tricky, "wb").close()
        try:
            self.assertNotIn("<script>", listing.render(MEDIA))
        finally:
            os.remove(tricky)


if __name__ == "__main__":
    unittest.main()
