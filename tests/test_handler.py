"""The desktop link handler in tools/.

Any web page can fire a vrplay: link at it, so what matters is what it refuses:
an address outside this machine, the LAN and the tailnet must never reach a
player, and nothing in the link may be read as a player option.
"""

import importlib.util
import os
import unittest
import urllib.parse

from .fixture import config

_spec = importlib.util.spec_from_file_location(
    "vrplay_handler", os.path.join(config.PROJECT_DIR, "tools",
                                   "vrplay_handler.py"))
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)


def link(url):
    return "vrplay:?url=" + urllib.parse.quote(url, safe="")


class TestAddresses(unittest.TestCase):
    def test_accepts_this_machine_the_lan_and_the_tailnet(self):
        for url in ("http://127.0.0.1:9095/a.mkv",
                    "http://localhost:9095/a.mkv",
                    "http://192.168.100.5:9095/a.mkv",
                    "http://10.1.2.3/a.mkv",
                    "http://100.92.205.122:9095/a.mkv",
                    "https://box.tail1234.ts.net/a.mkv",
                    "http://nas.local:9095/a.mkv"):
            self.assertEqual(handler.stream_url(link(url)), url, url)

    def test_refuses_the_open_internet(self):
        for url in ("http://8.8.8.8/a.mkv", "http://example.com/a.mkv",
                    "http://100.128.0.1/a.mkv",       # just outside the tailnet
                    "http://evil.ts.net.example.com/a.mkv"):
            self.assertIsNone(handler.stream_url(link(url)), url)

    def test_refuses_anything_that_is_not_http(self):
        for url in ("file:///etc/passwd", "ftp://192.168.1.1/a", "-f",
                    "--fullscreen", "smb://192.168.1.2/share", ""):
            self.assertIsNone(handler.stream_url(link(url)), url)

    def test_refuses_a_link_without_a_url(self):
        self.assertIsNone(handler.stream_url("vrplay:"))
        self.assertIsNone(handler.stream_url("vrplay:?other=1"))


class TestPlayerChoice(unittest.TestCase):
    def test_override_wins_and_may_be_missing(self):
        self.assertIsNone(handler.pick_player({"VRPLAY_PLAYER": "no-such-player"}))

    def test_something_is_found_when_the_override_names_a_real_command(self):
        self.assertTrue(handler.pick_player({"VRPLAY_PLAYER": "sh"}))


if __name__ == "__main__":
    unittest.main()
