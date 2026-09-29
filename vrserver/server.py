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
from .access import in_scope, ip_allowed, log_line
from .config import HOST, PORT, ROOT, THUMB_PREFIX
from .media import cache_key, make_thumb

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


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

    def send_bytes(self, payload, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        return io.BytesIO(payload)

    def reject_range(self, size, fileobj):
        fileobj.close()
        self.send_response(416)
        self.send_header("Content-Range", "bytes */%d" % size)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    # -- routes -----------------------------------------------------------

    def send_thumb(self, rel):
        path = self.safe_abs(urllib.parse.unquote(rel))
        if not path or not os.path.isfile(path):
            self.send_error(404, "No such file")
            return None
        if not in_scope(path):
            self.send_error(403, "Not a configured folder")
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

    def list_directory(self, path):
        try:
            page = listing.render(path)
        except OSError:
            self.send_error(403, "Directory listing not permitted")
            return None
        return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

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
        if req.startswith(THUMB_PREFIX):
            return self.send_thumb(req[len(THUMB_PREFIX):])

        path = self.translate_path(self.path)
        if not in_scope(path):
            self.send_error(403, "Not a configured folder")
            return None
        if os.path.isdir(path):
            return super().send_head()   # redirect, index.html, or listing
        return self.send_file(path)

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
