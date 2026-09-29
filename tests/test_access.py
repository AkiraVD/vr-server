"""The IP allow list and the scan-scope check."""

import os
import unittest

from .fixture import MEDIA, NESTED, OUTSIDE, ROOT, access


class TestAllowList(unittest.TestCase):
    def test_exact_address_allowed(self):
        self.assertTrue(access.ip_allowed("127.0.0.1"))

    def test_cidr_range_allowed(self):
        self.assertTrue(access.ip_allowed("10.4.5.6"))

    def test_unlisted_address_denied(self):
        self.assertFalse(access.ip_allowed("203.0.113.177"))

    def test_adjacent_address_denied(self):
        """A near miss must not pass: this is the whole point of the list."""
        self.assertFalse(access.ip_allowed("127.0.0.2"))
        self.assertFalse(access.ip_allowed("11.0.0.1"))

    def test_ipv4_mapped_ipv6_is_unwrapped(self):
        self.assertTrue(access.ip_allowed("::ffff:127.0.0.1"))

    def test_garbage_denied_not_crashing(self):
        self.assertFalse(access.ip_allowed("not-an-ip"))
        self.assertFalse(access.ip_allowed(""))

    def test_empty_list_allows_everyone(self):
        """Fails open by design, so a config typo cannot lock you out."""
        saved = access.ALLOW_NETS
        try:
            access.ALLOW_NETS = []
            self.assertTrue(access.ip_allowed("203.0.113.177"))
        finally:
            access.ALLOW_NETS = saved


class TestParseAllow(unittest.TestCase):
    def test_plain_and_cidr(self):
        nets = access.parse_allow(["203.0.113.5", "10.0.0.0/8"])
        self.assertEqual(len(nets), 2)

    def test_invalid_entries_are_dropped_not_fatal(self):
        nets = access.parse_allow(["203.0.113.5", "nonsense", "999.1.1.1"])
        self.assertEqual(len(nets), 1)

    def test_host_bits_tolerated(self):
        """strict=False, so 10.1.2.3/8 is accepted rather than rejected."""
        self.assertEqual(len(access.parse_allow(["10.1.2.3/8"])), 1)


class TestScope(unittest.TestCase):
    def test_root_itself_is_servable(self):
        self.assertTrue(access.in_scope(ROOT))

    def test_configured_folder_servable(self):
        self.assertTrue(access.in_scope(MEDIA))

    def test_below_configured_folder_servable(self):
        self.assertTrue(access.in_scope(NESTED))
        self.assertTrue(access.in_scope(os.path.join(NESTED, "deep.mkv")))

    def test_unconfigured_sibling_refused(self):
        self.assertFalse(access.in_scope(OUTSIDE))
        self.assertFalse(access.in_scope(os.path.join(OUTSIDE, "secret.mp4")))

    def test_prefix_collision_refused(self):
        """'media-other' must not pass merely because it starts with 'media'."""
        sibling = os.path.join(ROOT, "media-other")
        os.makedirs(sibling, exist_ok=True)
        self.assertFalse(access.in_scope(sibling))

    def test_scan_roots_stay_inside_root(self):
        for base in access.scan_roots():
            self.assertTrue(base.startswith(os.path.realpath(ROOT)))


if __name__ == "__main__":
    unittest.main()
