"""Who may connect, what they may reach, and the record of both.

Four related jobs:
  * the access log, written to console and optionally to a file
  * the IP allow list, so a stray device on the LAN cannot wander in
  * the scan scope, so only configured folders are servable at all
  * the protected folders, which need a password before they are served or
    even listed

The first three fail open when unconfigured. That is deliberate: this server is
normally driven from inside a headset, and a config typo that silently denied
everything would be very hard to diagnose from there.

Protection is the exception and fails *closed*: a folder named in `protect`
with no usable password is refused outright rather than served. The reasoning
that makes fail-open right for the others inverts here -- a typo that leaves
private content readable is far worse than one that locks you out of it, and
unlike the others this failure is obvious the moment you try to open the folder.
"""

import base64
import binascii
import datetime
import hashlib
import hmac
import ipaddress
import os
import sys
import threading
import time

from .config import (AUTH_HASH, AUTH_USER, CFG, COOKIE_KEY_PATH, COOKIE_NAME,
                     LOG_PATH, ROOT, UNLOCK_SECONDS, save_protect)

_LOG_LOCK = threading.Lock()


def log_line(msg):
    """Timestamped line to the console, and to the access log if configured."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s  %s" % (stamp, msg)
    with _LOG_LOCK:
        print(line, flush=True)
        if LOG_PATH:
            try:
                with open(LOG_PATH, "a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


def parse_allow(entries):
    """Accepts plain addresses and CIDR ranges alike."""
    nets = []
    for entry in entries:
        try:
            nets.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            print("allow: ignoring invalid entry %r" % entry, file=sys.stderr)
    return nets


ALLOW_NETS = parse_allow(CFG["allow"])


def ip_allowed(addr):
    """No list configured means no restriction, so a typo cannot lock you out."""
    if not ALLOW_NETS:
        return True
    try:
        ip = ipaddress.ip_address(addr.replace("::ffff:", ""))
    except ValueError:
        return False
    return any(ip in net for net in ALLOW_NETS)


_SCAN_ROOTS = None


def scan_roots():
    """Configured folders, resolved and constrained to ROOT. Computed once."""
    global _SCAN_ROOTS
    if _SCAN_ROOTS is not None:
        return _SCAN_ROOTS
    root = os.path.realpath(ROOT)
    out = []
    for entry in CFG["scan"]:
        target = os.path.realpath(
            entry if os.path.isabs(entry) else os.path.join(root, entry))
        if target != root and not target.startswith(root + os.sep):
            print("scan: skipping %r - outside root" % entry, file=sys.stderr)
            continue
        if not os.path.isdir(target):
            print("scan: skipping %r - not a directory" % entry, file=sys.stderr)
            continue
        out.append(target)
    _SCAN_ROOTS = out
    return out


def in_scope(path):
    """Servable? The root itself, or anything inside a configured folder."""
    if not CFG["scan"]:
        return True
    target = os.path.realpath(path)
    if target == os.path.realpath(ROOT):
        return True
    return any(target == base or target.startswith(base + os.sep)
               for base in scan_roots())


# -- protected folders ----------------------------------------------------

_PROTECT_ROOTS = None


def protect_roots():
    """Configured protected folders, resolved and constrained to ROOT.

    Computed once, like scan_roots. An entry that does not resolve inside ROOT
    is dropped with a warning -- it cannot match anything, so keeping it would
    only give a false sense of cover.
    """
    global _PROTECT_ROOTS
    if _PROTECT_ROOTS is not None:
        return _PROTECT_ROOTS
    root = os.path.realpath(ROOT)
    out = []
    for entry in CFG["protect"]:
        target = os.path.realpath(
            entry if os.path.isabs(entry) else os.path.join(root, entry))
        if target != root and not target.startswith(root + os.sep):
            print("protect: skipping %r - outside root" % entry, file=sys.stderr)
            continue
        out.append(target)
    _PROTECT_ROOTS = out
    return out


def is_protected(path):
    """Is this path a protected folder, or anything inside one?"""
    roots = protect_roots()
    if not roots:
        return False
    target = os.path.realpath(path)
    return any(target == base or target.startswith(base + os.sep)
               for base in roots)


def auth_configured():
    """A username and a parseable hash both present."""
    return bool(AUTH_USER) and bool(parse_hash(AUTH_HASH))


def parse_hash(stored):
    """(iterations, salt, digest) from the stored string, or None if unusable.

    Strict on purpose. b64decode without validate=True discards characters it
    does not recognise, so a mangled field decodes to b"" rather than failing --
    and an empty digest compares equal to the empty result of a zero-length
    derivation, which would let any password through. Every part is checked for
    being present and sane before it is trusted.
    """
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
    except (AttributeError, ValueError):
        return None
    if algo != "pbkdf2_sha256":
        return None
    try:
        iterations = int(iters)
        salt = base64.b64decode(salt_b64, validate=True)
        digest = base64.b64decode(hash_b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    if iterations < 1 or not salt or not digest:
        return None
    return iterations, salt, digest


def hash_password(password, iterations=600000, salt=None):
    """The stored form of a password. Used by --set-password, never at request time."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def check_password(password):
    """Constant-time comparison, so a wrong guess leaks nothing by timing."""
    parsed = parse_hash(AUTH_HASH)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                              iterations, len(expected))
    return hmac.compare_digest(got, expected)


def credentials_ok(user, password):
    """The username and password from the unlock form.

    Deliberately not HTTP Basic. A browser that has answered a Basic challenge
    keeps sending the Authorization header on every later request to the same
    origin, and there is no way to ask it to stop -- so "Lock again" could clear
    the cookie and the very next request would arrive authenticated anyway. A
    form and a cookie can actually be revoked.

    The password is checked even when the username is wrong, so both paths cost
    the same time and a wrong username is not distinguishable from a wrong
    password.
    """
    if not auth_configured():
        return False
    ok = check_password(password or "")
    if AUTH_USER:
        ok = hmac.compare_digest((user or "").encode("utf-8"),
                                 AUTH_USER.encode("utf-8")) and ok
    return ok


# -- the unlock cookie ----------------------------------------------------

_COOKIE_KEY = None
_KEY_LOCK = threading.Lock()


def cookie_key():
    """The HMAC key, created on first use and kept at 0600.

    Generated rather than configured: it is not something anyone should have to
    choose, and a file the server owns cannot be committed by accident the way
    a config value can. Deleting it invalidates every outstanding cookie, which
    is the blunt "sign everybody out" this server would otherwise lack.
    """
    global _COOKIE_KEY
    if _COOKIE_KEY is not None:
        return _COOKIE_KEY
    with _KEY_LOCK:
        if _COOKIE_KEY is not None:
            return _COOKIE_KEY
        try:
            with open(COOKIE_KEY_PATH, "rb") as fh:
                key = fh.read()
            if len(key) < 32:
                raise ValueError("short key")
        except (OSError, ValueError):
            key = os.urandom(32)
            try:
                fd = os.open(COOKIE_KEY_PATH,
                             os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
            except OSError as exc:
                # Not fatal: an in-memory key still works, it just does not
                # survive a restart. Saying so beats silently logging everyone
                # out on every restart with no explanation.
                print("unlock: cannot write %s (%s); cookies will not survive "
                      "a restart" % (COOKIE_KEY_PATH, exc), file=sys.stderr)
        _COOKIE_KEY = key
        return _COOKIE_KEY


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload):
    return _b64(hmac.new(cookie_key(), payload.encode("ascii"),
                         hashlib.sha256).digest())


def make_cookie(now=None):
    """A cookie value that is valid until UNLOCK_SECONDS from now."""
    now = int(time.time() if now is None else now)
    payload = _b64(str(now + UNLOCK_SECONDS).encode("ascii"))
    return "%s.%s" % (payload, _sign(payload))


def cookie_valid(value):
    """Signature intact and not expired.

    The signature is checked before the expiry is even read, so a forged
    payload never gets parsed.
    """
    if not value or "." not in value:
        return False
    payload, _, sig = value.rpartition(".")
    if not hmac.compare_digest(sig.encode("ascii"), _sign(payload).encode("ascii")):
        return False
    try:
        return int(_unb64(payload).decode("ascii")) > time.time()
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False


def cookie_from_header(header):
    """Our cookie's value out of a raw Cookie header, or None."""
    if not header:
        return None
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == COOKIE_NAME:
            return value
    return None


def protected_set():
    """The folders named in the config, normalised for comparison."""
    return {str(e).strip().strip("/") for e in CFG["protect"]}


def candidate_folders(max_depth=3, limit=400):
    """Folders that could be protected, as root-relative paths.

    A protected folder's children are not listed: they are already covered, so
    offering them would invite ticking twenty subfolders that a single parent
    entry already protects, and turn one tidy config line into twenty.

    Depth-capped and count-capped, because this feeds a checkbox list somebody
    reads in a headset -- a library with thousands of folders would be unusable
    long before it was slow.
    """
    root = os.path.realpath(ROOT)
    out = []

    def walk(path, rel, depth):
        if depth > max_depth or len(out) >= limit:
            return
        try:
            names = sorted(n for n in os.listdir(path) if not n.startswith("."))
        except OSError:
            return
        for name in names:
            full = os.path.join(path, name)
            if not os.path.isdir(full) or os.path.islink(full):
                continue
            child = "%s/%s" % (rel, name) if rel else name
            out.append(child)
            if len(out) >= limit:
                return
            # Stop here: everything below is already covered by this one.
            if not is_protected(full):
                walk(full, child, depth + 1)

    walk(root, "", 1)
    return out


def set_protect(entries):
    """Replace the protected list, in memory and on disk.

    Entries that do not resolve to a directory inside ROOT are dropped rather
    than stored: a path that cannot match anything would sit in the config
    looking like cover it does not provide.
    """
    global _PROTECT_ROOTS
    root = os.path.realpath(ROOT)
    kept = []
    for entry in entries:
        entry = str(entry).strip().strip("/")
        if not entry or "\x00" in entry:
            continue
        target = os.path.realpath(os.path.join(root, entry))
        if target == root or not target.startswith(root + os.sep):
            continue
        if not os.path.isdir(target):
            continue
        rel = os.path.relpath(target, root)
        if rel not in kept:
            kept.append(rel)
    kept.sort()

    CFG["protect"] = kept
    _PROTECT_ROOTS = None          # memo is stale the moment the list changes
    save_protect(kept)
    return kept
