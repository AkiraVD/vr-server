"""The HTTP layer.

The stdlib's SimpleHTTPRequestHandler ignores the Range header, which is fatal
here: without 206 responses a player cannot seek, and many refuse to start at
all. So send_head is replaced with one that answers ranges properly, and the
directory listing is replaced with the thumbnail grid.

GET and HEAD only. Nothing here writes.
"""

import io
import os
import re
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import listing
from .access import (auth_configured, cookie_from_header, cookie_valid,
                     credentials_ok, in_scope, ip_allowed, is_protected,
                     log_line, make_cookie, set_protect)
from .config import (COOKIE_NAME, HOST, LOCK_PATH, MAX_FORM_BYTES,
                     PLAYER_BUTTON, PLAYLIST_PREFIX, PORT, PROTECT_PATH, ROOT,
                     THUMB_PREFIX, UNLOCK_PATH, UNLOCK_SECONDS, VIDEO_EXTS)
from .media import cache_key, make_thumb

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
# Host goes into a URL inside the playlist, so only what a host:port can hold.
HOST_RE = re.compile(r"[A-Za-z0-9.\-]+(:\d{1,5})?|\[[0-9A-Fa-f:.]+\](:\d{1,5})?")


class Slice:
    """File wrapper that stops after `remaining` bytes, for copyfileobj."""

    def __init__(self, fileobj, remaining):
        self.fileobj = fileobj
        self.remaining = remaining

    def read(self, n=-1):
        if self.remaining <= 0:
            return b""
        if n is None or n < 0 or n > self.remaining:
            n = self.remaining
        data = self.fileobj.read(n)
        self.remaining -= len(data)
        return data

    def close(self):
        self.fileobj.close()


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mkv": "video/x-matroska",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".wmv": "video/x-ms-wmv",
    }

    # -- helpers ----------------------------------------------------------

    def log_message(self, fmt, *args):
        log_line("%s  %s" % (self.client_address[0], fmt % args))

    def safe_abs(self, rel):
        """Resolve a request-relative path inside ROOT, or None if it escapes."""
        root = os.path.realpath(ROOT)
        target = os.path.realpath(os.path.join(root, rel.lstrip("/")))
        if target == root or target.startswith(root + os.sep):
            return target
        return None

    def send_bytes(self, payload, ctype, status=200, no_store=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Cookie")
        self.end_headers()
        return io.BytesIO(payload)

    def origin(self):
        """scheme://host:port as this client reached us, or "" if unusable.

        Another process has to be told where to fetch the file, and the only
        address known to work is the one this request came in on (LAN, tailnet
        or MagicDNS). The Host header is client-supplied and ends up in a link
        and a playlist, so it is checked against what a host can contain.
        """
        host = self.headers.get("Host", "")
        if not HOST_RE.fullmatch(host):
            return ""
        scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" \
            else "http"
        return "%s://%s" % (scheme, host)

    def send_bytes_with(self, payload, ctype, extra):
        """send_bytes, plus headers, and never cached: the URL inside is
        built from this request's Host and would be wrong for another client."""
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        return io.BytesIO(payload)

    def authed(self):
        """Unlocked, by cookie or by credentials on this very request.

        The cookie is what makes the listing work: that page is not protected,
        so a browser would never send Basic credentials with it and the page
        could not tell locked from unlocked.
        """
        if not auth_configured():
            return False
        return cookie_valid(cookie_from_header(self.headers.get("Cookie")))

    def redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def send_unlock(self, failed=False):
        """Show the password form.

        Protected folders are hidden, so without a door to knock on you would
        have to remember a protected URL to be prompted at all.
        """
        if not auth_configured():
            self.send_error(403, "No password is configured")
            return None
        if self.authed():
            return self.redirect("/")
        page = listing.login_page(failed).encode("utf-8")
        return self.send_bytes(page, "text/html; charset=utf-8",
                               status=401 if failed else 200, no_store=True)

    def do_unlock(self, fields):
        """Check the form and hand back a cookie."""
        if not auth_configured():
            self.send_error(403, "No password is configured")
            return
        user = (fields.get("user") or [""])[0]
        password = (fields.get("password") or [""])[0]
        if not credentials_ok(user, password):
            log_line("UNLOCK FAILED %s" % self.client_address[0])
            self.send_unlock(failed=True)
            return

        log_line("UNLOCK %s" % self.client_address[0])
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", self.cookie_header(make_cookie(),
                                                          UNLOCK_SECONDS))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def cookie_header(self, value, max_age):
        """Set and clear must agree on every attribute or the clear is ignored.

        Secure only when the request actually arrived over TLS: Tailscale Serve
        terminates HTTPS and says so, while a plain-HTTP LAN client would
        silently drop a Secure cookie and never appear to unlock at all.
        """
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return ("%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict%s"
                % (COOKIE_NAME, value, max_age, secure))

    def send_lock(self):
        """Drop the cookie again, for handing the headset to someone else."""
        log_line("LOCK %s" % self.client_address[0])
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", self.cookie_header("", 0))
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def require_auth(self, path):
        """None if `path` may be served, otherwise the reply that refuses it.

        Returns a sentinel rather than writing and returning None, because
        send_head uses None to mean "nothing more to send".
        """
        if not is_protected(path):
            return None
        if not auth_configured():
            # Fails closed: a protected folder with no usable password is not
            # served at all. The alternative -- ignoring the setting -- would
            # publish exactly what it was meant to hide.
            log_line("PROTECTED %s  no password configured, refusing" % path)
            self.send_error(403, "Protected, but no password is configured")
            return False
        if self.authed():
            return None
        # The form, not a Basic challenge: a browser that answers a Basic
        # challenge keeps sending those credentials for the rest of the session,
        # which would make "Lock again" impossible to honour.
        self.send_response(303)
        self.send_header("Location", UNLOCK_PATH)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return False

    def reject_range(self, size, fileobj):
        fileobj.close()
        self.send_response(416)
        self.send_header("Content-Range", "bytes */%d" % size)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    # -- routes -----------------------------------------------------------

    def gated_file(self, rel):
        """Absolute path of a file this request may touch, else None (reply sent).

        The gates in one place, so a route added later cannot forget one.
        """
        path = self.safe_abs(urllib.parse.unquote(rel))
        if not path or not os.path.isfile(path):
            self.send_error(404, "No such file")
            return None
        if not in_scope(path):
            self.send_error(403, "Not a configured folder")
            return None
        if self.require_auth(path) is False:
            return None
        return path

    def send_thumb(self, rel):
        # A thumbnail is a picture of the file. Gating the video but not its
        # preview would hand over the thing worth hiding.
        path = self.gated_file(rel)
        if not path:
            return None

        key = cache_key(path)
        thumb = make_thumb(path, key) if key else None
        if not thumb:
            self.send_error(404, "No thumbnail")
            return None
        try:
            with open(thumb, "rb") as fh:
                blob = fh.read()
        except OSError:
            self.send_error(404, "No thumbnail")
            return None

        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        return io.BytesIO(blob)

    def send_playlist(self, rel):
        """A one-line .m3u pointing at the raw file, for the device's own player.

        The player then streams the file itself, so HEVC, E-AC-3 and embedded
        subtitle and audio tracks all work, and nothing is converted here.
        """
        if not PLAYER_BUTTON:
            self.send_error(404, "Not available")
            return None
        path = self.gated_file(rel)
        if not path:
            return None
        if os.path.splitext(path)[1].lower() not in VIDEO_EXTS:
            self.send_error(404, "Not a video")
            return None
        if is_protected(path):
            # The player will not carry the unlock cookie, so it would be
            # redirected to the form and fail with no explanation.
            self.send_error(403, "Protected files cannot be opened this way")
            return None

        origin = self.origin()
        if not origin:
            self.send_error(400, "Bad Host header")
            return None
        name = os.path.basename(path)
        url = "%s/%s" % (origin, urllib.parse.quote(
            os.path.relpath(path, os.path.realpath(ROOT))))
        # A filename may legally hold a newline, which would end the title line.
        title = " ".join(name.split())
        body = ("#EXTM3U\n#EXTINF:-1,%s\n%s\n" % (title, url)).encode("utf-8")
        payload = self.send_bytes_with(
            body, "audio/x-mpegurl",
            {"Content-Disposition":
             "inline; filename*=UTF-8''%s.m3u" % urllib.parse.quote(name)})
        return payload

    def list_directory(self, path):
        try:
            page = listing.render(path, authed=self.authed(),
                                  origin=self.origin(),
                                  agent=self.headers.get("User-Agent", ""))
        except OSError:
            self.send_error(403, "Directory listing not permitted")
            return None
        # What this page contains depends on the unlock cookie, so a cached copy
        # is a wrong copy: locking would redirect you to a stale page still
        # showing the unlocked library, and appear to do nothing at all.
        return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8",
                               no_store=True)

    def send_file(self, path):
        """A file, whole or as a byte range."""
        try:
            fileobj = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None
        try:
            st = os.fstat(fileobj.fileno())
            size = st.st_size
            header = self.headers.get("Range")
            start, end, partial_req = 0, size - 1, False

            if header:
                match = RANGE_RE.fullmatch(header.strip())
                if not match or (not match.group(1) and not match.group(2)):
                    return self.reject_range(size, fileobj)
                first, last = match.group(1), match.group(2)
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))   # suffix range
                if start >= size or start > end:
                    return self.reject_range(size, fileobj)
                partial_req = True

            length = end - start + 1
            self.send_response(206 if partial_req else 200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if partial_req:
                self.send_header("Content-Range",
                                 "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Last-Modified", self.date_time_string(st.st_mtime))
            self.end_headers()
            fileobj.seek(start)
            return Slice(fileobj, length)
        except Exception:
            fileobj.close()
            raise

    # -- dispatch ---------------------------------------------------------

    def send_head(self):
        peer = self.client_address[0]
        if not ip_allowed(peer):
            log_line("DENIED %s  %s" % (peer, self.path))
            self.send_error(403, "Not allowed from this address")
            return None

        req = urllib.parse.urlsplit(self.path).path
        if req == UNLOCK_PATH:
            return self.send_unlock()
        if req == LOCK_PATH:
            return self.send_lock()
        if req.startswith(THUMB_PREFIX):
            return self.send_thumb(req[len(THUMB_PREFIX):])
        if req.startswith(PLAYLIST_PREFIX):
            return self.send_playlist(req[len(PLAYLIST_PREFIX):])

        path = self.translate_path(self.path)
        if not in_scope(path):
            self.send_error(403, "Not a configured folder")
            return None
        if self.require_auth(path) is False:
            return None
        if os.path.isdir(path):
            return super().send_head()   # redirect, index.html, or listing
        return self.send_file(path)

    def read_form(self):
        """Parsed form body, or None if it was refused."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400, "Bad length")
            return None
        if length > MAX_FORM_BYTES:
            self.send_error(413, "Too much form data")
            return None
        body = self.rfile.read(length) if length else b""
        return urllib.parse.parse_qs(body.decode("utf-8", "replace"))

    def save_protect(self):
        """Replace the protected list. The only write this server accepts.

        Requires the unlock *cookie* specifically, not merely valid credentials.
        The cookie is SameSite=Strict, so a form on another site cannot cause
        the browser to send it, which is what stops a hostile page unlocking
        your library behind your back.
        """
        if not auth_configured():
            self.send_error(403, "No password is configured")
            return
        if not cookie_valid(cookie_from_header(self.headers.get("Cookie"))):
            self.send_error(403, "Unlock first")
            return

        fields = self.read_form()
        if fields is None:
            return
        kept = set_protect(fields.get("protect", []))
        log_line("PROTECT %s  now: %s" % (self.client_address[0],
                                          ", ".join(kept) or "(nothing)"))

        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        peer = self.client_address[0]
        if not ip_allowed(peer):
            log_line("DENIED %s  %s" % (peer, self.path))
            self.send_error(403, "Not allowed from this address")
            return
        try:
            req = urllib.parse.urlsplit(self.path).path
            if req == UNLOCK_PATH:
                self.do_unlock(self.read_form())
            elif req == PROTECT_PATH:
                self.save_protect()
            else:
                self.send_error(404, "Nothing here accepts a POST")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self):
        # Players abort mid-transfer on every seek; that is normal, not an error.
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_HEAD(self):
        try:
            super().do_HEAD()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def create_server():
    """A threading server rooted at ROOT.

    Without directory=ROOT the handler resolves against the process working
    directory, which would serve whatever folder the script was launched from.
    """
    server = ThreadingHTTPServer((HOST, PORT), partial(Handler, directory=ROOT))
    server.daemon_threads = True
    return server
