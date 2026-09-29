"""Video inspection and the thumbnail cache.

Thumbnails are cached under THUMB_DIR, keyed by a hash of the file's path,
mtime and size, so replacing a file invalidates its thumbnail automatically.
Metadata from ffprobe is cached beside it as JSON.

VR video is stored full side-by-side: both eyes in one 2:1 frame. A plain frame
grab is therefore two horizontally squashed copies of the same picture, so for
~2:1 sources only the left eye is cropped and scaled.
"""

import hashlib
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from .access import scan_roots
from .config import (MAX_FFMPEG, SBS_MIN_ASPECT, SEEK_FRACTION, THUMB_DIR,
                     THUMB_WIDTH, VIDEO_EXTS)

# Caps total concurrent ffmpeg processes across both the startup pass and any
# on-demand render, so opening a folder cannot swamp the machine.
_GEN_SEM = threading.BoundedSemaphore(MAX_FFMPEG)
_KEY_LOCKS = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key):
    """One lock per cache key, so two viewers never render the same thumb twice."""
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = _KEY_LOCKS[key] = threading.Lock()
        return lock


def cache_key(path):
    """Identity of a file's thumbnail. Includes mtime+size so edits invalidate."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    raw = "%s|%d|%d" % (os.path.realpath(path), st.st_mtime_ns, st.st_size)
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()


def probe(path, key):
    """Width/height/duration, cached next to the thumbnail as JSON."""
    meta_file = os.path.join(THUMB_DIR, key + ".json")
    try:
        with open(meta_file) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass

    meta = {"width": 0, "height": 0, "duration": 0.0}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        ).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        meta["width"] = int(stream.get("width") or 0)
        meta["height"] = int(stream.get("height") or 0)
        meta["duration"] = float((data.get("format") or {}).get("duration") or 0)
    except Exception:
        pass

    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
        tmp = meta_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(meta, fh)
        os.replace(tmp, meta_file)
    except OSError:
        pass
    return meta


def is_sbs(meta):
    """Does this frame hold both eyes side by side?"""
    height = meta.get("height") or 0
    width = meta.get("width") or 0
    return bool(height and width / height >= SBS_MIN_ASPECT)


def _grab(path, at, vf, tmp):
    """One ffmpeg frame grab. True if it produced a non-empty image."""
    try:
        with _GEN_SEM:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-ss", "%.2f" % at, "-i", path,
                 "-frames:v", "1", "-vf", vf, "-q:v", "4", "-y", tmp],
                capture_output=True, timeout=180,
            )
        return proc.returncode == 0 and os.path.getsize(tmp) > 0
    except Exception:
        return False


def make_thumb(path, key):
    """Render the thumbnail if absent. Returns its path, or None on failure."""
    dest = os.path.join(THUMB_DIR, key + ".jpg")
    if os.path.exists(dest):
        return dest

    with _key_lock(key):
        if os.path.exists(dest):       # won by another thread while waiting
            return dest

        meta = probe(path, key)
        duration = meta.get("duration") or 0.0
        seek = duration * SEEK_FRACTION if duration > 0 else 300.0

        # Left eye only for side-by-side sources.
        vf = "crop=iw/2:ih:0:0," if is_sbs(meta) else ""
        vf += "scale=%d:-2" % THUMB_WIDTH

        tmp = os.path.join(THUMB_DIR, key + ".tmp.jpg")
        try:
            os.makedirs(THUMB_DIR, exist_ok=True)
            # Retry from the start: duration can be wrong or absent, and a seek
            # past the last frame silently produces nothing.
            for at in (seek, 0.0):
                if _grab(path, at, vf, tmp):
                    os.replace(tmp, dest)
                    return dest
        except Exception:
            pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return None


def find_videos(roots):
    """Every video under `roots`, de-duplicated: scan entries may nest."""
    videos, seen = [], set()
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                    continue
                full = os.path.join(dirpath, name)
                if full not in seen:
                    seen.add(full)
                    videos.append(full)
    return videos


def warm_cache():
    """Check the configured folders on startup and render what is missing.

    Runs on a background thread; the server must never wait on it.
    """
    roots = scan_roots()
    if not roots:
        print("thumbnails: no scan folders configured, nothing pre-rendered",
              flush=True)
        return

    for base in roots:
        print("scanning: %s" % base, flush=True)

    videos = find_videos(roots)
    todo = []
    for path in videos:
        key = cache_key(path)
        if key and not os.path.exists(os.path.join(THUMB_DIR, key + ".jpg")):
            todo.append((path, key))

    print("thumbnails: %d video(s), %d cached, %d to render"
          % (len(videos), len(videos) - len(todo), len(todo)), flush=True)
    if not todo:
        return

    done = 0
    counter_lock = threading.Lock()

    def work(item):
        nonlocal done
        make_thumb(*item)
        with counter_lock:
            done += 1
            print("  [%d/%d] %s" % (done, len(todo), os.path.basename(item[0])),
                  flush=True)

    with ThreadPoolExecutor(max_workers=MAX_FFMPEG) as pool:
        list(pool.map(work, todo))
    print("thumbnails: done", flush=True)
