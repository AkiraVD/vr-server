"""A temporary media tree, and a config pointing at it.

vrserver reads its configuration once at import time, so the environment has
to be set before any of it is imported. This module does that, then imports
the package and re-exports it: importing from here guarantees the order is
right no matter which test module runs first.
"""

import atexit
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
for directory in (MEDIA, NESTED, OUTSIDE):
    os.makedirs(directory)

# 2048 bytes of a repeating pattern: any byte range is trivial to predict.
CLIP_BYTES = bytes(range(256)) * 8
CLIP = os.path.join(MEDIA, "clip.mp4")
DEEP = os.path.join(NESTED, "deep.mkv")
SECRET = os.path.join(OUTSIDE, "secret.mp4")
NOTES = os.path.join(MEDIA, "notes.txt")
SUBS = os.path.join(MEDIA, "clip.srt")

for path in (CLIP, DEEP, SECRET):
    with open(path, "wb") as fh:
        fh.write(CLIP_BYTES)
for path in (NOTES, SUBS):
    with open(path, "w") as fh:
        fh.write("not a video")

CONFIG = {
    "root": ROOT,
    "host": "127.0.0.1",
    "port": 0,
    "thumbs": os.path.join(TMP, "thumbs"),
    "log": "",
    "scan": ["media"],
    "allow": ["127.0.0.1", "10.0.0.0/8"],
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

__all__ = ["TMP", "ROOT", "MEDIA", "NESTED", "OUTSIDE", "CLIP", "DEEP",
           "SECRET", "NOTES", "SUBS", "CLIP_BYTES", "CONFIG", "CONFIG_PATH",
           "access", "config", "listing", "media", "server"]
