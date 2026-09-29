"""End-to-end HTTP: Range handling, refusals, and routing.

Range support is the reason this server exists rather than `python3 -m
http.server`, and it is where the awkward edge cases live, so it is tested
against a real running server rather than by inspecting the regex.
"""

import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

from .fixture import CLIP_BYTES, access, config, server

_httpd = None
_base = None
_real_log = None


def setUpModule():
    """Start a real server on an ephemeral port, with its logging silenced."""
    global _httpd, _base, _real_log
    _real_log = access.log_line
    access.log_line = lambda msg: None
    server.log_line = access.log_line

    _httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(server.Handler, directory=config.ROOT))
    _httpd.daemon_threads = True
    _base = "http://127.0.0.1:%d" % _httpd.server_address[1]
    threading.Thread(target=_httpd.serve_forever, daemon=True).start()


def tearDownModule():
    _httpd.shutdown()
    _httpd.server_close()
    access.log_line = _real_log
    server.log_line = _real_log


def fetch(path, headers=None):
    """Returns (status, headers, body), turning error responses into values."""
    req = urllib.request.Request(_base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        with exc:                       # close it, or Python warns on cleanup
            return exc.code, dict(exc.headers), exc.read()


class TestWholeFile(unittest.TestCase):
    def test_full_body_when_no_range_asked(self):
        status, headers, body = fetch("/media/clip.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, CLIP_BYTES)
        self.assertEqual(int(headers["Content-Length"]), len(CLIP_BYTES))

    def test_advertises_range_support(self):
        """Players check this before trying to seek."""
        _, headers, _ = fetch("/media/clip.mp4")
        self.assertEqual(headers["Accept-Ranges"], "bytes")


class TestRangeRequests(unittest.TestCase):
    def test_leading_range(self):
        status, headers, body = fetch("/media/clip.mp4",
                                      {"Range": "bytes=0-9"})
        self.assertEqual(status, 206)
        self.assertEqual(body, CLIP_BYTES[0:10])
        self.assertEqual(headers["Content-Range"],
                         "bytes 0-9/%d" % len(CLIP_BYTES))

    def test_mid_file_range(self):
        status, _, body = fetch("/media/clip.mp4", {"Range": "bytes=100-199"})
        self.assertEqual(status, 206)
        self.assertEqual(body, CLIP_BYTES[100:200])

    def test_open_ended_range_runs_to_eof(self):
        status, _, body = fetch("/media/clip.mp4", {"Range": "bytes=2000-"})
        self.assertEqual(status, 206)
        self.assertEqual(body, CLIP_BYTES[2000:])

    def test_suffix_range_returns_the_tail(self):
        """bytes=-5 means the LAST five bytes, not the first five."""
        status, _, body = fetch("/media/clip.mp4", {"Range": "bytes=-5"})
        self.assertEqual(status, 206)
        self.assertEqual(body, CLIP_BYTES[-5:])

    def test_end_beyond_eof_is_clamped(self):
        size = len(CLIP_BYTES)
        status, headers, body = fetch("/media/clip.mp4",
                                      {"Range": "bytes=2040-999999"})
        self.assertEqual(status, 206)
        self.assertEqual(body, CLIP_BYTES[2040:])
        self.assertEqual(headers["Content-Range"],
                         "bytes 2040-%d/%d" % (size - 1, size))

    def test_start_past_eof_refused(self):
        status, headers, _ = fetch("/media/clip.mp4",
                                   {"Range": "bytes=999999-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */%d" % len(CLIP_BYTES))

    def test_malformed_range_refused(self):
        for bad in ("bytes=abc", "bytes=-", "chunks=0-5", "bytes=5-1"):
            with self.subTest(header=bad):
                status, _, _ = fetch("/media/clip.mp4", {"Range": bad})
                self.assertEqual(status, 416)


class TestRouting(unittest.TestCase):
    def test_root_listing_served(self):
        status, headers, body = fetch("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"media", body)

    def test_root_hides_unconfigured_folder(self):
        _, _, body = fetch("/")
        self.assertNotIn(b"outside", body)

    def test_unconfigured_folder_refused(self):
        self.assertEqual(fetch("/outside/")[0], 403)

    def test_file_in_unconfigured_folder_refused(self):
        self.assertEqual(fetch("/outside/secret.mp4")[0], 403)

    def test_thumbnail_of_unconfigured_file_refused(self):
        """Scope is checked before any ffmpeg work is started."""
        self.assertEqual(fetch("/_thumb/outside/secret.mp4")[0], 403)

    def test_traversal_refused(self):
        self.assertIn(fetch("/../../../etc/passwd")[0], (403, 404))

    def test_missing_file_is_404(self):
        self.assertEqual(fetch("/media/nope.mp4")[0], 404)

    def test_head_matches_get_headers(self):
        req = urllib.request.Request(_base + "/media/clip.mp4", method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(int(resp.headers["Content-Length"]),
                             len(CLIP_BYTES))


if __name__ == "__main__":
    unittest.main()
