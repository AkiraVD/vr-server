"""The directory page.

Rendered for a browser running inside a headset, so the design is dark, the
tap targets are large, and every card carries a thumbnail: reading filenames
through a VR display is unpleasant, recognising a picture is not.

Only folders and playable video appear. Subtitles, cover art and stray text
files cannot be opened by a VR player, so they would be pure clutter.
"""

import html
import os
import urllib.parse

from .access import scan_roots
from .config import CFG, ROOT, THUMB_PREFIX, VIDEO_EXTS
from .media import cache_key, is_sbs, probe

CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 20px 24px 60px;
  background: #14161a; color: #e8eaed;
  font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; word-break: break-word; }
.sub { color: #8b929c; font-size: 14px; margin-bottom: 22px; }
.up {
  display: inline-block; margin-bottom: 22px; padding: 12px 22px;
  background: #22262e; border: 1px solid #333944; border-radius: 10px;
  color: #cfd4db; text-decoration: none; font-size: 16px;
}
.grid {
  display: grid; gap: 18px;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}
a.card {
  display: block; background: #1c1f26; border: 1px solid #2a2f38;
  border-radius: 12px; overflow: hidden; text-decoration: none; color: inherit;
}
.shot { position: relative; aspect-ratio: 16 / 9; background: #0e1013; }
.shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.folder {
  display: flex; align-items: center; justify-content: center;
  height: 100%; font-size: 54px; color: #6c7686;
}
.badge {
  position: absolute; bottom: 8px; right: 8px;
  background: rgba(0,0,0,.78); color: #fff;
  padding: 3px 8px; border-radius: 6px; font-size: 13px;
}
.tag {
  position: absolute; top: 8px; left: 8px;
  background: rgba(56,120,220,.9); color: #fff;
  padding: 3px 8px; border-radius: 6px; font-size: 12px; font-weight: 600;
}
.meta { padding: 12px 14px 14px; }
.name { font-size: 15px; line-height: 1.35; word-break: break-word; }
.size { color: #8b929c; font-size: 13px; margin-top: 5px; }
"""

PAGE = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s</title><style>%s</style></head><body>"
        "<h1>%s</h1><div class='sub'>%d item(s)</div>%s"
        "<div class='grid'>%s</div></body></html>")


def human_size(n):
    """Byte count as a short human string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit in ("B", "KB"):
                return "%.0f %s" % (n, unit)
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def human_time(seconds):
    """Seconds as H:MM:SS, or M:SS under an hour. Empty when unknown."""
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def visible_names(path, at_root):
    """Entries worth showing: configured folders at the root, video below."""
    names = [n for n in os.listdir(path) if not n.startswith(".")]

    # At the top level show only folders config asks for, so unrelated trees
    # (game assets, downloads) stay out of the way in the headset.
    if at_root and CFG["scan"]:
        allowed = scan_roots()

        def listed(name):
            full = os.path.realpath(os.path.join(path, name))
            if not os.path.isdir(full):
                return True              # files are filtered by type below
            return any(a == full or a.startswith(full + os.sep)
                       for a in allowed)

        names = [n for n in names if listed(n)]

    names = [n for n in names
             if os.path.isdir(os.path.join(path, n))
             or os.path.splitext(n)[1].lower() in VIDEO_EXTS]
    names.sort(key=lambda n: (not os.path.isdir(os.path.join(path, n)),
                              n.lower()))
    return names


def folder_card(link, safe_name):
    return ('<a class="card" href="%s/">'
            '<div class="shot"><div class="folder">&#128193;</div></div>'
            '<div class="meta"><div class="name">%s</div></div></a>'
            % (link, safe_name))


def video_card(full, link, safe_name, root):
    """A card carrying the thumbnail, duration, size and a 3D marker."""
    rel = os.path.relpath(full, root)
    key = cache_key(full)
    meta = probe(full, key) if key else {}

    duration = human_time(meta.get("duration"))
    tag = '<div class="tag">3D SBS</div>' if is_sbs(meta) else ""
    badge = '<div class="badge">%s</div>' % duration if duration else ""
    try:
        size = human_size(os.path.getsize(full))
    except OSError:
        size = ""
    res = ""
    if meta.get("width"):
        res = " &middot; %d&times;%d" % (meta["width"], meta["height"])

    return ('<a class="card" href="%s">'
            '<div class="shot">'
            '<img loading="lazy" src="%s%s" alt="">%s%s</div>'
            '<div class="meta"><div class="name">%s</div>'
            '<div class="size">%s%s</div></div></a>'
            % (link, THUMB_PREFIX, urllib.parse.quote(rel), tag, badge,
               safe_name, size, res))


def render(path):
    """The full HTML page for a directory. Raises OSError if unreadable."""
    root = os.path.realpath(ROOT)
    here = os.path.realpath(path)
    rel_here = "" if here == root else os.path.relpath(here, root)

    names = visible_names(path, at_root=(here == root))
    # At the top level, name the page after the configured root rather than a
    # fixed word, so the heading follows config.json instead of the code.
    display = html.escape(rel_here or os.path.basename(root) or "/")

    cards = []
    for name in names:
        full = os.path.join(path, name)
        link = urllib.parse.quote(name)
        safe_name = html.escape(name)
        if os.path.isdir(full):
            cards.append(folder_card(link, safe_name))
        else:
            cards.append(video_card(full, link, safe_name, root))

    up = '<a class="up" href="..">&#8592; Up</a>' if rel_here else ""
    return PAGE % (display, CSS, display, len(names), up, "".join(cards))
