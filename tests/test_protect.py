"""Password-protected folders.

Three things are being asserted here. That the gate holds -- no listing, no
file, no thumbnail without the password. That it fails *closed*, the one place
this server deliberately departs from the fail-open rule the allow list and
scan scope follow. And that unlocking can be undone: authentication is by form
and cookie precisely because HTTP Basic credentials, once given, keep arriving
on every later request and cannot be taken back.
"""

import base64
import json
import threading
import time
import urllib.parse
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

from .fixture import (AUTH_PASSWORD, AUTH_USER, CONFIG, MEDIA, NESTED, PRIVATE,
                      access, config, listing, media, server)

_httpd = None
_base = None
_real_log = None


def setUpModule():
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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Let the 303 surface instead of being followed, so it can be inspected."""

    def redirect_request(self, *args, **kwargs):
        return None


def login(user=None, password=None):
    """POST the unlock form; returns (status, Set-Cookie header or "")."""
    body = urllib.parse.urlencode({
        "user": AUTH_USER if user is None else user,
        "password": AUTH_PASSWORD if password is None else password,
    }).encode()
    req = urllib.request.Request(_base + config.UNLOCK_PATH, data=body,
                                 method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    opener = urllib.request.build_opener(NoRedirect())
    try:
        resp = opener.open(req)
        return resp.status, resp.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Set-Cookie", "")


def fetch(path, cookie=None):
    """(status, body). Redirects are not followed and refusals are not raised:
    a locked path answers 303-to-the-form, and that is the thing under test."""
    req = urllib.request.Request(_base + path)
    if cookie:
        req.add_header("Cookie", cookie)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def cookie_header():
    return "%s=%s" % (config.COOKIE_NAME, access.make_cookie())


class TestPasswordHashing(unittest.TestCase):
    def test_round_trip(self):
        stored = access.hash_password("hunter2 hunter2", iterations=1000)
        self.assertTrue(stored.startswith("pbkdf2_sha256$1000$"))
        parsed = access.parse_hash(stored)
        self.assertIsNotNone(parsed)

    def test_same_password_hashes_differently_each_time(self):
        # A random salt, so two accounts with one password are not obviously so.
        a = access.hash_password("the same password", iterations=1000)
        b = access.hash_password("the same password", iterations=1000)
        self.assertNotEqual(a, b)

    def test_garbage_hash_is_rejected_not_crashed(self):
        for bad in ("", "nonsense", "md5$1$a$b", "pbkdf2_sha256$x$y$z",
                    "pbkdf2_sha256$1000$!!!$!!!", "pbkdf2_sha256$0$AAAA$AAAA",
                    "pbkdf2_sha256$1000$$", None):
            self.assertIsNone(access.parse_hash(bad))

    def test_an_empty_digest_cannot_authenticate_anything(self):
        # b64decode drops invalid characters unless told not to, so "!!!" once
        # decoded to b"" -- and an empty digest matches an empty derivation,
        # letting every password through. It must parse as unusable instead.
        saved = access.AUTH_HASH
        try:
            for bad in ("pbkdf2_sha256$1000$!!!$!!!", "pbkdf2_sha256$1000$$"):
                access.AUTH_HASH = bad
                self.assertFalse(access.check_password("anything at all"))
                self.assertFalse(access.check_password(""))
                self.assertFalse(access.auth_configured())
        finally:
            access.AUTH_HASH = saved

    def test_check_password_matches_only_the_right_one(self):
        self.assertTrue(access.check_password(AUTH_PASSWORD))
        self.assertFalse(access.check_password(AUTH_PASSWORD + "!"))
        self.assertFalse(access.check_password(""))


class TestCredentials(unittest.TestCase):
    def test_accepts_the_configured_pair(self):
        self.assertTrue(access.credentials_ok(AUTH_USER, AUTH_PASSWORD))

    def test_rejects_wrong_password_and_wrong_user(self):
        self.assertFalse(access.credentials_ok(AUTH_USER, "wrong"))
        self.assertFalse(access.credentials_ok("nobody", AUTH_PASSWORD))

    def test_rejects_empty_and_missing(self):
        for user, password in ((None, None), ("", ""), (AUTH_USER, None),
                               (AUTH_USER, "")):
            self.assertFalse(access.credentials_ok(user, password))


class TestScope(unittest.TestCase):
    def test_the_folder_and_its_contents_are_protected(self):
        self.assertTrue(access.is_protected(PRIVATE))
        self.assertTrue(access.is_protected(PRIVATE + "/locked.mp4"))

    def test_siblings_are_not(self):
        self.assertFalse(access.is_protected(MEDIA))
        self.assertFalse(access.is_protected(MEDIA + "/clip.mp4"))

    def test_entries_outside_root_are_dropped(self):
        original = config.CFG["protect"]
        access._PROTECT_ROOTS = None
        try:
            config.CFG["protect"] = ["../../etc", "media/private"]
            roots = access.protect_roots()
            self.assertEqual(len(roots), 1)
            self.assertTrue(roots[0].endswith("private"))
        finally:
            config.CFG["protect"] = original
            access._PROTECT_ROOTS = None


class TestListingHidesProtected(unittest.TestCase):
    def test_absent_without_the_password(self):
        page = listing.render(MEDIA)
        self.assertNotIn("private", page)

    def test_present_with_it(self):
        page = listing.render(MEDIA, authed=True)
        self.assertIn("private", page)

    def test_the_grid_omits_protected_files(self):
        self.assertNotIn("locked.mp4", listing.videos(PRIVATE, False))
        self.assertIn("locked.mp4", listing.videos(PRIVATE, False, authed=True))


class TestHttpGate(unittest.TestCase):
    def test_listing_of_a_protected_folder_needs_the_password(self):
        self.assertEqual(fetch("/media/private/")[0], 303)

    def test_file_needs_the_password(self):
        self.assertEqual(fetch("/media/private/locked.mp4")[0], 303)

    def test_a_protected_path_sends_you_to_the_form(self):
        # Never a Basic challenge: those credentials could not be revoked.
        req = urllib.request.Request(_base + "/media/private/locked.mp4")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            opener.open(req)
            self.fail("expected a redirect")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 303)
            self.assertEqual(exc.headers.get("Location"), config.UNLOCK_PATH)
            self.assertIsNone(exc.headers.get("WWW-Authenticate"))

    def test_the_cookie_gets_the_file(self):
        status, body = fetch("/media/private/locked.mp4", cookie=cookie_header())
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 2048)

    def test_a_wrong_password_mints_no_cookie(self):
        status, setter = login(password="not it")
        self.assertEqual(status, 401)
        self.assertEqual(setter, "")

    def test_thumbnail_of_a_protected_file_is_refused(self):
        # The picture is the thing worth hiding; gating only the video leaks it.
        self.assertEqual(fetch("/_thumb/media/private/locked.mp4")[0], 303)

    def test_unprotected_content_is_untouched(self):
        status, body = fetch("/media/clip.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 2048)

    def test_range_requests_work_once_unlocked(self):
        req = urllib.request.Request(_base + "/media/private/locked.mp4")
        req.add_header("Cookie", cookie_header())
        req.add_header("Range", "bytes=0-9")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(len(resp.read()), 10)


class TestFailsClosed(unittest.TestCase):
    """The deliberate inversion of this project's fail-open convention."""

    def setUp(self):
        self._user, self._hash = access.AUTH_USER, access.AUTH_HASH

    def tearDown(self):
        access.AUTH_USER, access.AUTH_HASH = self._user, self._hash

    def test_no_password_configured_refuses_rather_than_serves(self):
        access.AUTH_USER, access.AUTH_HASH = "", ""
        self.assertFalse(access.auth_configured())
        status, _ = fetch("/media/private/locked.mp4")
        self.assertEqual(status, 403)

    def test_unparseable_hash_also_refuses(self):
        access.AUTH_HASH = "not-a-real-hash"
        self.assertFalse(access.auth_configured())
        status, _ = fetch("/media/private/locked.mp4")
        self.assertEqual(status, 403)

    def test_even_a_valid_cookie_cannot_get_in(self):
        # No half-open state: without a usable hash nothing opens the folder.
        cookie = cookie_header()          # minted while the hash was still good
        access.AUTH_HASH = ""
        status, _ = fetch("/media/private/locked.mp4", cookie=cookie)
        self.assertEqual(status, 403)


class TestStartupPassSkipsProtected(unittest.TestCase):
    def test_protected_video_is_not_pre_rendered(self):
        found = media.find_videos([MEDIA])
        self.assertFalse([p for p in found if "private" in p])


class TestUnlockCookie(unittest.TestCase):
    def test_round_trip(self):
        self.assertTrue(access.cookie_valid(access.make_cookie()))

    def test_expired_is_rejected(self):
        stale = access.make_cookie(now=time.time() - config.UNLOCK_SECONDS - 10)
        self.assertFalse(access.cookie_valid(stale))

    def test_tampering_is_rejected(self):
        good = access.make_cookie()
        payload, _, sig = good.rpartition(".")
        # A payload swapped for a later expiry must not survive the signature.
        forged = access._b64(str(int(time.time()) + 999999).encode()) + "." + sig
        self.assertFalse(access.cookie_valid(forged))
        self.assertFalse(access.cookie_valid(payload + ".deadbeef"))

    def test_garbage_is_rejected_not_crashed(self):
        for bad in (None, "", "nodot", ".", "a.b", "!!!.!!!"):
            self.assertFalse(access.cookie_valid(bad))

    def test_header_parsing_picks_our_cookie_out(self):
        value = access.make_cookie()
        header = "other=1; %s=%s; third=x" % (config.COOKIE_NAME, value)
        self.assertEqual(access.cookie_from_header(header), value)
        self.assertIsNone(access.cookie_from_header("other=1"))
        self.assertIsNone(access.cookie_from_header(None))


class TestUnlockRoute(unittest.TestCase):
    def test_it_serves_a_form_not_a_basic_challenge(self):
        status, body = fetch(config.UNLOCK_PATH)
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertIn("name='password'", page)
        self.assertIn("method='post'", page)

    def test_the_form_sets_a_cookie_and_redirects(self):
        status, cookie = login()
        self.assertEqual(status, 303)
        self.assertIn(config.COOKIE_NAME + "=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_a_wrong_user_is_refused_too(self):
        status, setter = login(user="someone-else")
        self.assertEqual(status, 401)
        self.assertEqual(setter, "")

    def test_the_cookie_alone_opens_a_protected_file(self):
        # The whole point: no Authorization header, just the cookie.
        req = urllib.request.Request(_base + "/media/private/locked.mp4")
        req.add_header("Cookie", "%s=%s" % (config.COOKIE_NAME,
                                            access.make_cookie()))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

    def test_the_cookie_reveals_the_folder_in_the_listing(self):
        req = urllib.request.Request(_base + "/media/")
        req.add_header("Cookie", "%s=%s" % (config.COOKIE_NAME,
                                            access.make_cookie()))
        with urllib.request.urlopen(req) as resp:
            self.assertIn("private", resp.read().decode())

    def test_an_expired_cookie_does_not(self):
        stale = access.make_cookie(now=time.time() - config.UNLOCK_SECONDS - 10)
        status, _ = fetch("/media/private/locked.mp4",
                          cookie="%s=%s" % (config.COOKIE_NAME, stale))
        self.assertEqual(status, 303)

    def test_the_listing_is_never_cached(self):
        # Its content depends on the cookie, so a cached copy is a wrong copy:
        # locking would land on a stale page still showing the unlocked
        # library, and look like the button did nothing.
        req = urllib.request.Request(_base + "/media/")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
            self.assertEqual(resp.headers.get("Vary"), "Cookie")

    def test_set_and_clear_agree_on_the_cookie_attributes(self):
        # A clear that differs on Secure or SameSite is silently ignored.
        def attrs(header):
            return {p.strip().split("=")[0].lower()
                    for p in header.split(";")[1:]}

        opener = urllib.request.build_opener(NoRedirect())
        _, setter = login()
        try:
            opener.open(urllib.request.Request(_base + config.LOCK_PATH))
            self.fail("expected a redirect")
        except urllib.error.HTTPError as exc:
            clearer = exc.headers.get("Set-Cookie")
        self.assertEqual(attrs(setter), attrs(clearer))

    def test_lock_clears_the_cookie(self):
        req = urllib.request.Request(_base + config.LOCK_PATH)
        opener = urllib.request.build_opener(NoRedirect())
        try:
            opener.open(req)
            self.fail("expected a redirect")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 303)
            self.assertIn("Max-Age=0", exc.headers.get("Set-Cookie", ""))


class TestLockControl(unittest.TestCase):
    def test_offered_when_locked(self):
        page = listing.render(MEDIA)
        self.assertIn(config.UNLOCK_PATH, page)

    def test_becomes_a_way_out_once_unlocked(self):
        page = listing.render(MEDIA, authed=True)
        self.assertIn(config.LOCK_PATH, page)

    def test_absent_when_no_password_is_configured(self):
        saved = access.AUTH_HASH
        try:
            access.AUTH_HASH = ""
            page = listing.render(MEDIA)
            self.assertNotIn(config.UNLOCK_PATH, page)
        finally:
            access.AUTH_HASH = saved


def post(path, data, cookie=None):
    body = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(_base + path, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if cookie:
        req.add_header("Cookie", "%s=%s" % (config.COOKIE_NAME, cookie))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        resp = opener.open(req)
        return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


class TestProtectWrite(unittest.TestCase):
    """The only route that writes. Everything about it is a refusal by default."""

    def setUp(self):
        self._saved = list(config.CFG["protect"])

    def tearDown(self):
        access.set_protect(self._saved)

    def test_refused_without_a_cookie(self):
        self.assertEqual(post(config.PROTECT_PATH, {"protect": ["media"]}), 403)
        self.assertEqual(config.CFG["protect"], self._saved)

    def test_the_password_in_the_body_is_not_a_substitute(self):
        # Only the cookie counts. It is SameSite=Strict, which is what makes a
        # cross-site form post fail to carry it.
        self.assertEqual(
            post(config.PROTECT_PATH,
                 {"protect": ["media"], "user": AUTH_USER,
                  "password": AUTH_PASSWORD}), 403)
        self.assertEqual(config.CFG["protect"], self._saved)

    def test_an_expired_cookie_is_refused(self):
        stale = access.make_cookie(now=time.time() - config.UNLOCK_SECONDS - 10)
        self.assertEqual(
            post(config.PROTECT_PATH, {"protect": ["media"]}, cookie=stale), 403)

    def test_unlocked_can_change_the_list(self):
        status = post(config.PROTECT_PATH, {"protect": ["media/nested"]},
                      cookie=access.make_cookie())
        self.assertEqual(status, 303)
        self.assertEqual(config.CFG["protect"], ["media/nested"])
        self.assertTrue(access.is_protected(NESTED))
        self.assertFalse(access.is_protected(PRIVATE))

    def test_clearing_every_box_unlocks_everything(self):
        status = post(config.PROTECT_PATH, {}, cookie=access.make_cookie())
        self.assertEqual(status, 303)
        self.assertEqual(config.CFG["protect"], [])
        self.assertFalse(access.is_protected(PRIVATE))

    def test_paths_outside_root_are_dropped_not_stored(self):
        post(config.PROTECT_PATH,
             {"protect": ["../../etc", "/etc", "media/private"]},
             cookie=access.make_cookie())
        self.assertEqual(config.CFG["protect"], ["media/private"])

    def test_a_path_that_is_not_a_directory_is_dropped(self):
        post(config.PROTECT_PATH, {"protect": ["media/clip.mp4"]},
             cookie=access.make_cookie())
        self.assertEqual(config.CFG["protect"], [])

    def test_duplicates_collapse(self):
        post(config.PROTECT_PATH,
             {"protect": ["media/private", "media/private/", "media/private"]},
             cookie=access.make_cookie())
        self.assertEqual(config.CFG["protect"], ["media/private"])

    def test_the_change_is_persisted_to_disk(self):
        post(config.PROTECT_PATH, {"protect": ["media/nested"]},
             cookie=access.make_cookie())
        with open(config.CONFIG_PATH) as fh:
            self.assertEqual(json.load(fh)["protect"], ["media/nested"])

    def test_writing_protect_leaves_the_other_keys_alone(self):
        post(config.PROTECT_PATH, {"protect": []}, cookie=access.make_cookie())
        with open(config.CONFIG_PATH) as fh:
            written = json.load(fh)
        self.assertEqual(written["auth_user"], AUTH_USER)
        self.assertEqual(written["root"], CONFIG["root"])
        self.assertEqual(written["allow"], CONFIG["allow"])

    def test_the_gate_reacts_immediately_without_a_restart(self):
        # The memo behind is_protected must not outlive the list it caches.
        post(config.PROTECT_PATH, {"protect": []}, cookie=access.make_cookie())
        self.assertEqual(fetch("/media/private/locked.mp4")[0], 200)
        post(config.PROTECT_PATH, {"protect": ["media/private"]},
             cookie=access.make_cookie())
        self.assertEqual(fetch("/media/private/locked.mp4")[0], 303)

    def test_other_routes_still_refuse_a_post(self):
        self.assertEqual(post("/media/", {}, cookie=access.make_cookie()), 404)


class TestConfigModal(unittest.TestCase):
    def test_hidden_until_unlocked(self):
        page = listing.render(MEDIA)
        self.assertNotIn(config.PROTECT_PATH, page)

    def test_offered_once_unlocked(self):
        page = listing.render(MEDIA, authed=True)
        self.assertIn(config.PROTECT_PATH, page)
        self.assertIn("#config", page)

    def test_lists_folders_with_the_protected_ones_ticked(self):
        page = listing.render(MEDIA, authed=True)
        # Attributes are single-quoted throughout this page's HTML.
        self.assertIn("value='media/private' checked", page)
        self.assertIn("value='media'", page)

    def test_children_of_a_locked_folder_are_not_offered(self):
        # media/private is protected, so anything inside it is already covered
        # and listing it would invite redundant entries.
        import os
        os.makedirs(os.path.join(PRIVATE, "deeper"), exist_ok=True)
        rels = access.candidate_folders()
        self.assertIn("media/private", rels)
        self.assertNotIn("media/private/deeper", rels)

    def test_a_tick_means_explicitly_listed_not_merely_covered(self):
        saved = list(config.CFG["protect"])
        try:
            access.set_protect(["media"])       # covers media/private too
            page = listing.render(MEDIA, authed=True)
            self.assertIn("value='media' checked", page)
            # private is covered by media, so it is not offered at all now.
            self.assertNotIn("value='media/private'", page)
        finally:
            access.set_protect(saved)

    def test_candidate_folders_stay_inside_root(self):
        for rel in access.candidate_folders():
            self.assertFalse(rel.startswith("/"))
            self.assertNotIn("..", rel)


class TestLockingActuallyRevokes(unittest.TestCase):
    """The reason this uses a form rather than HTTP Basic.

    A browser that answers a Basic challenge keeps sending those credentials on
    every later request to the same origin, and nothing can ask it to stop --
    so clearing the cookie would be undone by the very next request and "Lock
    again" could never work. Only the cookie may grant access.
    """

    def test_an_authorization_header_grants_nothing(self):
        raw = ("%s:%s" % (AUTH_USER, AUTH_PASSWORD)).encode()
        header = "Basic " + base64.b64encode(raw).decode("ascii")
        req = urllib.request.Request(_base + "/media/private/locked.mp4")
        req.add_header("Authorization", header)
        opener = urllib.request.build_opener(NoRedirect())
        try:
            opener.open(req)
            self.fail("credentials in a header must not unlock anything")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 303)

    def test_the_listing_stays_locked_with_such_a_header(self):
        raw = ("%s:%s" % (AUTH_USER, AUTH_PASSWORD)).encode()
        header = "Basic " + base64.b64encode(raw).decode("ascii")
        req = urllib.request.Request(_base + "/media/")
        req.add_header("Authorization", header)
        with urllib.request.urlopen(req) as resp:
            self.assertNotIn("private", resp.read().decode())

    def test_locking_then_reloading_shows_the_locked_library(self):
        # The whole round trip: unlock, confirm, lock, confirm it took.
        _, setter = login()
        value = setter.split(";")[0].split("=", 1)[1]
        cookie = "%s=%s" % (config.COOKIE_NAME, value)
        self.assertEqual(fetch("/media/private/locked.mp4", cookie=cookie)[0], 200)

        opener = urllib.request.build_opener(NoRedirect())
        try:
            req = urllib.request.Request(_base + config.LOCK_PATH)
            req.add_header("Cookie", cookie)
            opener.open(req)
            self.fail("expected a redirect")
        except urllib.error.HTTPError as exc:
            cleared = exc.headers.get("Set-Cookie", "")
        self.assertIn("Max-Age=0", cleared)
        # A browser applying that header sends nothing afterwards.
        self.assertEqual(fetch("/media/private/locked.mp4")[0], 303)


if __name__ == "__main__":
    unittest.main()
