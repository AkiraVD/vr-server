"""A temporary media tree, and a config pointing at it.

vrserver reads its configuration once at import time, so the environment has
to be set before any of it is imported. This module does that, then imports
the package and re-exports it: importing from here guarantees the order is
right no matter which test module runs first.
"""

import atexit
import base64
import hashlib
import json
import os
import shutil
import tempfile

TMP = tempfile.mkdtemp(prefix="vrserver-test-")
atexit.register(shutil.rmtree, TMP, True)

ROOT = os.path.join(TMP, "root")
MEDIA = os.path.join(ROOT, "media")            # in scan
NESTED = os.path.join(MEDIA, "nested")         # below a scan folder
OUTSIDE = os.path.join(ROOT, "outside")        # NOT in scan
PRIVATE = os.path.join(MEDIA, "private")       # in scan, but protected
for directory in (MEDIA, NESTED, OUTSIDE, PRIVATE):
    os.makedirs(directory)

# 2048 bytes of a repeating pattern: any byte range is trivial to predict.
CLIP_BYTES = bytes(range(256)) * 8
CLIP = os.path.join(MEDIA, "clip.mp4")
DEEP = os.path.join(NESTED, "deep.mkv")
SECRET = os.path.join(OUTSIDE, "secret.mp4")
NOTES = os.path.join(MEDIA, "notes.txt")
SUBS = os.path.join(MEDIA, "clip.srt")
LOCKED = os.path.join(PRIVATE, "locked.mp4")

for path in (CLIP, DEEP, SECRET, LOCKED):
    with open(path, "wb") as fh:
        fh.write(CLIP_BYTES)
for path in (NOTES, SUBS):
    with open(path, "w") as fh:
        fh.write("not a video")

# A real PBKDF2 hash, but at 1000 iterations rather than the 600000 the
# --set-password command uses: the suite verifies the mechanism, and should not
# spend a tenth of a second on every single request it makes.
AUTH_USER = "tester"
AUTH_PASSWORD = "a good long password"
_SALT = b"0123456789abcdef"
_ITERATIONS = 1000
AUTH_HASH = "pbkdf2_sha256$%d$%s$%s" % (
    _ITERATIONS,
    base64.b64encode(_SALT).decode("ascii"),
    base64.b64encode(hashlib.pbkdf2_hmac(
        "sha256", AUTH_PASSWORD.encode(), _SALT, _ITERATIONS)).decode("ascii"),
)

CONFIG = {
    "root": ROOT,
    "host": "127.0.0.1",
    "port": 0,
    "thumbs": os.path.join(TMP, "thumbs"),
    "log": "",
    "scan": ["media"],
    "allow": ["127.0.0.1", "10.0.0.0/8"],
    "protect": ["media/private"],
    "auth_user": AUTH_USER,
    "auth_hash": AUTH_HASH,
    "thumb_width": 320,
    "seek_fraction": 0.5,
    "max_ffmpeg": 1,
}

CONFIG_PATH = os.path.join(TMP, "config.json")
with open(CONFIG_PATH, "w") as fh:
    json.dump(CONFIG, fh)

os.environ["VRSERVE_CONFIG"] = CONFIG_PATH

# Imported only after the environment is in place.
from vrserver import access, config, listing, media, server   # noqa: E402

__all__ = ["TMP", "ROOT", "MEDIA", "NESTED", "OUTSIDE", "PRIVATE", "CLIP",
           "DEEP", "SECRET", "NOTES", "SUBS", "LOCKED", "CLIP_BYTES",
           "AUTH_USER", "AUTH_PASSWORD", "AUTH_HASH", "CONFIG", "CONFIG_PATH",
           "access", "config", "listing", "media", "server"]
