"""Settings.

Precedence is config.json, then environment variables on top. A missing or
malformed config warns and falls back to defaults rather than refusing to
start: the server is usually reached from inside a headset, where an error
you cannot read is worse than a working server with default settings.
"""

import json
import os
import sys

# The package lives one level below the project root, where config.json sits.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CONFIG_PATH = os.environ.get(
    "VRSERVE_CONFIG", os.path.join(PROJECT_DIR, "config.json")
)

DEFAULTS = {
    # Only a fallback: config.json is where the real path belongs. Kept inside
    # the project so a fresh clone with no config fails with a path that is
    # obviously a placeholder, rather than one that silently names a machine.
    "root": os.path.join(PROJECT_DIR, "media"),
    "host": "0.0.0.0",
    "port": 8080,
    "thumbs": os.path.join(PROJECT_DIR, ".thumbs"),
    "scan": [],             # folders pre-rendered on startup, walked recursively
    "allow": [],            # IPs/CIDRs permitted to connect; empty = everyone
    "protect": [],          # folders needing a password; empty = nothing hidden
    "auth_user": "",        # username for protected folders
    "auth_hash": "",        # pbkdf2_sha256$iters$salt$hash - set with --set-password
    "log": "",              # access log path; empty = console only
    "thumb_width": 480,
    "seek_fraction": 0.5,   # grab a frame from the middle of the video
    "max_ffmpeg": 3,        # concurrent ffmpeg processes, total
    "player_button": True,  # show "open in the device's video player" on cards
    "desktop_scheme": True, # desktop Play uses a vrplay: link (needs tools/install-
                            # player-handler.sh); false gives an .m3u download instead
}

ENV_OVERRIDES = (
    ("root", "VRSERVE_ROOT"),
    ("host", "VRSERVE_HOST"),
    ("port", "VRSERVE_PORT"),
    ("thumbs", "VRSERVE_THUMBS"),
)

NUMERIC_KEYS = ("port", "thumb_width", "seek_fraction", "max_ffmpeg")


def load_config(path):
    """Read `path`, apply environment overrides, coerce types. Never fatal."""
    cfg = dict(DEFAULTS)
    try:
        with open(path) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            print("config: ignoring unknown key(s): %s"
                  % ", ".join(sorted(unknown)), file=sys.stderr)
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except FileNotFoundError:
        print("config: none at %s, using defaults" % path, file=sys.stderr)
    except Exception as exc:
        print("config: %s unreadable (%s), using defaults" % (path, exc),
              file=sys.stderr)

    for key, var in ENV_OVERRIDES:
        if os.environ.get(var):
            cfg[key] = os.environ[var]

    try:
        cfg["port"] = int(cfg["port"])
        cfg["thumb_width"] = int(cfg["thumb_width"])
        cfg["seek_fraction"] = float(cfg["seek_fraction"])
        cfg["max_ffmpeg"] = max(1, int(cfg["max_ffmpeg"]))
    except (TypeError, ValueError) as exc:
        print("config: bad numeric value (%s), using defaults" % exc,
              file=sys.stderr)
        for key in NUMERIC_KEYS:
            cfg[key] = DEFAULTS[key]

    # A string like "false" is truthy; accept only a real boolean so a quoted
    # value cannot silently leave the button on.
    for key in ("player_button", "desktop_scheme"):
        if not isinstance(cfg[key], bool):
            print("config: %r must be true or false, using the default" % key,
                  file=sys.stderr)
            cfg[key] = DEFAULTS[key]

    for key in ("scan", "allow", "protect"):
        if not isinstance(cfg[key], list):
            print("config: %r must be a list, ignoring" % key, file=sys.stderr)
            cfg[key] = []
    return cfg


def save_protect(entries):
    """Persist just the `protect` list back to config.json.

    The one thing the HTTP layer may write, and it writes nothing else: the
    file is re-read, only this key is replaced, and the result is swapped in
    atomically so a crash mid-write cannot leave a truncated config. Mode 0600
    is preserved because the same file holds the password hash.
    """
    try:
        with open(CONFIG_PATH) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    except FileNotFoundError:
        data = {}
    data["protect"] = list(entries)

    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)


CFG = load_config(CONFIG_PATH)

# Deliberately not a config key: start.sh owns this tag. If it lived in the
# config, editing it would orphan an already-running server, which is the exact
# process the tag exists to find.
DEFAULT_APPNAME = "vr-server"


def argv_appname(argv):
    """-appname/--appname TAG, so the tag is visible in this process's argv."""
    for i, arg in enumerate(argv):
        if arg in ("-appname", "--appname") and i + 1 < len(argv):
            return argv[i + 1]
    return None


APPNAME = argv_appname(sys.argv[1:]) or DEFAULT_APPNAME

ROOT = CFG["root"]
HOST = CFG["host"]
PORT = CFG["port"]
THUMB_DIR = CFG["thumbs"]
THUMB_WIDTH = CFG["thumb_width"]
MAX_FFMPEG = CFG["max_ffmpeg"]
SEEK_FRACTION = CFG["seek_fraction"]
LOG_PATH = CFG["log"]
AUTH_USER = CFG["auth_user"]
AUTH_HASH = CFG["auth_hash"]
PLAYER_BUTTON = CFG["player_button"]
DESKTOP_SCHEME = CFG["desktop_scheme"]

# URL prefix the thumbnail route answers on.
THUMB_PREFIX = "/_thumb/"

# A one-line playlist for handing a video to the device's own player (VLC and
# friends). The browser cannot decode HEVC or E-AC-3 audio; a native player can.
PLAYLIST_PREFIX = "/_m3u/"

# Unlocking. Basic auth alone cannot drive this: the listing page is not itself
# protected, so a browser has no reason to send credentials with it and the page
# could never tell an unlocked visitor from a locked one. /_unlock checks the
# password once and sets a signed cookie that every later request carries.
UNLOCK_PATH = "/_unlock"
# The only route that writes anything, and only the `protect` list.
PROTECT_PATH = "/_protect"
MAX_FORM_BYTES = 64 * 1024
LOCK_PATH = "/_lock"
COOKIE_NAME = "vrs_unlock"
UNLOCK_SECONDS = 12 * 3600
# Beside config.json, and 0600: anyone holding this key can mint a valid cookie.
COOKIE_KEY_PATH = os.path.join(PROJECT_DIR, ".unlock.key")

# Width/height at or above this means the frame holds both eyes side by side.
SBS_MIN_ASPECT = 1.9

# One list drives listings, thumbnails, and the startup scan.
VIDEO_EXTS = {
    ".mp4", ".mkv", ".m4v", ".webm", ".mov", ".avi", ".wmv", ".ts", ".m2ts",
}
