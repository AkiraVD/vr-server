"""The Play button: hand a video to the device's own player.

The browser cannot decode HEVC or E-AC-3, but VLC and friends can, so the card
offers a link that starts one on the raw stream. What is asserted: the link is
right for each kind of device, the playlist route sits behind the same gates as
the file, protected files are never offered (a player cannot send the unlock
cookie), the Host header cannot smuggle anything into a playlist, and that the
card's own raw link is untouched.
"""

import http.client
import os
import threading
import unittest
import urllib.parse
from functools import partial
from http.server import ThreadingHTTPServer

from .fixture import (MEDIA, PRIVATE, TMP, access, config, listing, server)

_httpd = None
_port = None
_real_log = None

ORIGIN = "http://100.92.205.122:9095"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel) Chrome/120 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"
DESKTOP = "Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36"
QUEST = ("Mozilla/5.0 (X11; Linux x86_64; Android 12; Quest 3) "
         "OculusBrowser/35.0 Chrome/120 VR Safari/537.36")

ODD = os.path.join(MEDIA, "two\nlines.mp4")


def setUpModule():
    global _httpd, _port, _real_log
    with open(ODD, "wb") as fh:
        fh.write(b"x")
    _real_log = access.log_line
    access.log_line = lambda msg: None
    server.log_line = access.log_line
    _httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(server.Handler, directory=config.ROOT))
    _httpd.daemon_threads = True
    _port = _httpd.server_address[1]
    threading.Thread(target=_httpd.serve_forever, daemon=True).start()


def tearDownModule():
    _httpd.shutdown()
    _httpd.server_close()
    os.remove(ODD)
    access.log_line = _real_log
    server.log_line = _real_log


def request(path, headers=None):
    """(status, headers, body); http.client so the Host header is ours to set."""
    conn = http.client.HTTPConnection("127.0.0.1", _port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


class TestPlaylistRoute(unittest.TestCase):
    def test_points_at_the_raw_file(self):
        status, headers, body = request(
            "/_m3u/media/clip.mp4", {"Host": "100.92.205.122:9095"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "audio/x-mpegurl")
        self.assertEqual(
            body.decode(),
            "#EXTM3U\n#EXTINF:-1,clip.mp4\n"
            "http://100.92.205.122:9095/media/clip.mp4\n")

    def test_uses_the_address_the_client_came_in_on(self):
        _, _, body = request("/_m3u/media/clip.mp4",
                             {"Host": "box.tail1234.ts.net:9095"})
        self.assertIn(b"http://box.tail1234.ts.net:9095/media/clip.mp4", body)

    def test_https_when_a_tls_front_says_so(self):
        _, _, body = request("/_m3u/media/clip.mp4",
                             {"Host": "box.ts.net", "X-Forwarded-Proto": "https"})
        self.assertIn(b"https://box.ts.net/media/clip.mp4", body)

    def test_never_cached(self):
        """The URL inside depends on who asked."""
        _, headers, _ = request("/_m3u/media/clip.mp4")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_a_newline_in_a_filename_cannot_split_the_title(self):
        _, _, body = request("/_m3u/media/" + urllib.parse.quote("two\nlines.mp4"))
        lines = body.decode().split("\n")
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(lines[1], "#EXTINF:-1,two lines.mp4")
        self.assertTrue(lines[2].startswith("http://"))
        self.assertEqual(len(lines), 4)          # trailing newline

    def test_a_hostile_host_header_is_refused(self):
        # (A newline cannot be sent by http.client at all; the pattern refuses
        # it too, since fullmatch does not let `$` swallow a trailing one.)
        for bad in ("evil.com/x#EXTINF", "a b", "x;y", "http://evil", ""):
            status, _, _ = request("/_m3u/media/clip.mp4", {"Host": bad})
            self.assertEqual(status, 400, bad)

    def test_traversal_is_refused(self):
        self.assertIn(request("/_m3u/../../../etc/passwd")[0], (403, 404))


class TestPlaylistGates(unittest.TestCase):
    def test_outside_scan_is_refused(self):
        self.assertEqual(request("/_m3u/outside/secret.mp4")[0], 403)

    def test_missing_file_is_404(self):
        self.assertEqual(request("/_m3u/media/nope.mp4")[0], 404)

    def test_a_non_video_is_not_playlisted(self):
        self.assertEqual(request("/_m3u/media/notes.txt")[0], 404)

    def test_protected_without_the_cookie_goes_to_the_form(self):
        status, headers, _ = request("/_m3u/media/private/locked.mp4")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], config.UNLOCK_PATH)

    def test_protected_is_refused_even_when_unlocked(self):
        """The player would be bounced to the form and fail with no message."""
        cookie = "%s=%s" % (config.COOKIE_NAME, access.make_cookie())
        status, _, _ = request("/_m3u/media/private/locked.mp4",
                               {"Cookie": cookie})
        self.assertEqual(status, 403)


class TestButtonLinks(unittest.TestCase):
    def card(self, agent, origin=ORIGIN):
        return listing.render(MEDIA, origin=origin, agent=agent)

    def test_android_launches_vlc_directly(self):
        page = self.card(ANDROID)
        self.assertIn("href='intent://100.92.205.122:9095/media/clip.mp4#Intent;"
                      "scheme=http;package=org.videolan.vlc;type=video/*;",
                      page)

    def test_android_falls_back_to_the_playlist(self):
        page = self.card(ANDROID)
        want = urllib.parse.quote(ORIGIN + "/_m3u/media/clip.mp4", safe="")
        self.assertIn("S.browser_fallback_url=" + want, page)

    def test_ios_uses_the_vlc_scheme(self):
        self.assertIn("href='vlc://%s/media/clip.mp4'" % ORIGIN,
                      self.card(IPHONE))

    def test_desktop_gets_a_link_that_starts_the_player(self):
        want = "vrplay:?url=" + urllib.parse.quote(ORIGIN + "/media/clip.mp4",
                                                   safe="")
        self.assertIn("href='%s'" % want, self.card(DESKTOP))

    def test_desktop_can_fall_back_to_the_playlist(self):
        real = listing.DESKTOP_SCHEME
        listing.DESKTOP_SCHEME = False
        try:
            page = self.card(DESKTOP)
        finally:
            listing.DESKTOP_SCHEME = real
        self.assertIn("href='/_m3u/media/clip.mp4'", page)

    def test_the_quest_browser_gets_no_button(self):
        page = self.card(QUEST)
        self.assertNotIn("class='play'", page)
        self.assertIn("<a class='open' href='clip.mp4'>", page)

    def test_without_a_usable_host_it_still_offers_the_playlist(self):
        for agent in (ANDROID, DESKTOP):
            self.assertIn("href='/_m3u/media/clip.mp4'",
                          self.card(agent, origin=""))

    def test_the_card_still_links_to_the_raw_file(self):
        for agent in (ANDROID, IPHONE, DESKTOP, QUEST):
            self.assertIn("<a class='open' href='clip.mp4'>", self.card(agent))

    def test_the_button_is_not_nested_inside_the_raw_link(self):
        page = self.card(DESKTOP)
        card = page[page.index("<div class='card'>"):]
        card = card[:card.index("</a>") + 4]
        self.assertNotIn("class='play'", card)

    def test_protected_cards_are_never_offered(self):
        page = listing.render(PRIVATE, authed=True, origin=ORIGIN, agent=DESKTOP)
        self.assertIn("locked.mp4", page)
        self.assertNotIn("class='play'", page)


class TestButtonOff(unittest.TestCase):
    def setUp(self):
        self.real = (listing.PLAYER_BUTTON, server.PLAYER_BUTTON)
        listing.PLAYER_BUTTON = server.PLAYER_BUTTON = False

    def tearDown(self):
        listing.PLAYER_BUTTON, server.PLAYER_BUTTON = self.real

    def test_no_button(self):
        page = listing.render(MEDIA, origin=ORIGIN, agent=DESKTOP)
        self.assertNotIn("class='play'", page)

    def test_no_route(self):
        self.assertEqual(request("/_m3u/media/clip.mp4")[0], 404)

    def test_raw_files_still_serve(self):
        self.assertEqual(request("/media/clip.mp4")[0], 200)


class TestConfig(unittest.TestCase):
    def test_on_by_default(self):
        self.assertTrue(config.DEFAULTS["player_button"])
        self.assertTrue(config.DEFAULTS["desktop_scheme"])

    def test_a_non_boolean_falls_back_to_the_default(self):
        import json
        path = os.path.join(TMP, "button-cfg.json")
        with open(path, "w") as fh:
            json.dump({"player_button": "false", "desktop_scheme": 0}, fh)
        cfg = config.load_config(path)
        self.assertTrue(cfg["player_button"])
        self.assertTrue(cfg["desktop_scheme"])


if __name__ == "__main__":
    unittest.main()
