"""The directory page.

Rendered for a browser running inside a headset, so the type is large, the tap
targets are generous, and every card carries a thumbnail: reading filenames
through a VR display is unpleasant, recognising a picture is not. The palette
is close to black because a bright page is tiring to look at up close.

Folders and video are kept apart. The rail on the left is the directory tree
and the grid holds video only, so moving around the library never means
hunting for a folder card among the clips.

Only playable video appears in the grid. Subtitles, cover art and stray text
files cannot be opened by a VR player, so they would be pure clutter.

Everything is inline. There is no build step and no CDN, because the headset
often reaches this server over a LAN or tailnet with no route to the internet.
"""

import html
import os
import urllib.parse

from .access import scan_roots
from .config import CFG, ROOT, THUMB_PREFIX, VIDEO_EXTS
from .media import cache_key, is_sbs, probe

CSS = """
:root {
  --bg: #05060a;
  --rail: #0a0c11;
  --surface: #0f1218;
  --surface-hi: #161a22;
  --line: #191d26;
  --line-hi: #2b323e;
  --text: #e4e9f0;
  --dim: #7f8795;
  --accent: #5b9bff;
  --r: 13px;
  --rail-w: 252px;
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 400 17px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}

.wrap { display: flex; align-items: flex-start; min-height: 100vh; }

/* ---- rail: the directory tree ---------------------------------------- */

aside {
  position: sticky; top: 0;
  width: var(--rail-w); flex: none;
  height: 100vh; overflow-y: auto;
  background: var(--rail);
  border-right: 1px solid var(--line);
  padding-bottom: 28px;
}

.brand {
  position: sticky; top: 0; z-index: 2;
  padding: 19px 18px 15px;
  background: var(--rail);
  border-bottom: 1px solid var(--line);
  font-size: 12px; font-weight: 600;
  letter-spacing: .17em; text-transform: uppercase;
  color: var(--dim);
}

.tree { padding: 10px 10px 0; }

/* Each level indents, and the rule down the left makes the nesting legible
   at a glance -- there is no expand control to hint at depth. */
.branch {
  margin-left: 15px; padding-left: 9px;
  border-left: 1px solid var(--line);
}

.node {
  display: flex; align-items: center; gap: 9px;
  padding: 10px 11px; margin-bottom: 2px;
  border-radius: 9px;
  color: var(--dim); text-decoration: none;
  font-size: 15px; line-height: 1.3;
}
.node span {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.node svg { width: 17px; height: 17px; flex: none; }
.node:hover { background: var(--surface-hi); color: var(--text); }
.node.on {
  background: rgba(91, 155, 255, .13);
  color: var(--text); font-weight: 600;
}
.node.on svg { color: var(--accent); }
.node.open { color: var(--text); }

/* ---- page ------------------------------------------------------------ */

section { flex: 1; min-width: 0; }

header {
  position: sticky; top: 0; z-index: 5;
  padding: 19px 30px;
  background: rgba(5, 6, 10, .9);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--line);
}

.crumbs {
  display: flex; flex-wrap: wrap; align-items: center;
  gap: 1px 5px; margin-bottom: 5px;
  font-size: 14px; color: var(--dim);
}
.crumbs a {
  color: var(--dim); text-decoration: none;
  padding: 6px 9px; margin: -6px 0; border-radius: 8px;
}
.crumbs a:hover { color: var(--text); background: var(--surface-hi); }
.crumbs .sep { opacity: .4; }
.crumbs .here { color: var(--text); font-weight: 600; padding: 6px 0; }

h1 {
  margin: 0; font-size: 26px; font-weight: 650;
  letter-spacing: -.02em; word-break: break-word;
}
.count {
  margin-top: 4px; font-size: 14px; color: var(--dim);
  font-variant-numeric: tabular-nums;
}

main { padding: 26px 30px 70px; }

.grid {
  display: grid; gap: 20px;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
}

/* ---- cards ----------------------------------------------------------- */

.card {
  display: block; overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r);
  color: inherit; text-decoration: none;
  transition: transform .16s ease, border-color .16s ease, background .16s ease;
}
.card:hover {
  transform: translateY(-3px);
  border-color: var(--line-hi);
  background: var(--surface-hi);
}
.card:active { transform: translateY(-1px); }
.card:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }

.shot {
  position: relative;
  aspect-ratio: 16 / 9;
  background: #080a0e;
  overflow: hidden;
}

/* The glyph underneath is the placeholder. Thumbnails render on demand, so
   the first visit to a folder can wait seconds on ffmpeg, and one that never
   renders at all leaves a deliberate-looking icon rather than a broken frame.
   Stacking rather than scripting also keeps the page script-free. */
.shot img {
  position: relative; z-index: 1;
  width: 100%; height: 100%;
  object-fit: cover; display: block;
}

@keyframes breathe {
  0%, 100% { opacity: .26; }
  50%      { opacity: .48; }
}

.glyph {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: #39424f;
}
.glyph svg {
  width: 56px; height: 56px;
  animation: breathe 2.4s ease-in-out infinite;
}

/* Keeps the duration legible over a bright frame. */
.scrim {
  position: absolute; inset: auto 0 0 0; height: 46%; z-index: 2;
  background: linear-gradient(to top, rgba(0,0,0,.76), transparent);
  pointer-events: none;
}

.dur, .chip {
  position: absolute; z-index: 3;
  font-size: 13px; font-weight: 600; line-height: 1;
  padding: 6px 9px; border-radius: 7px;
  font-variant-numeric: tabular-nums;
}
.dur { right: 10px; bottom: 10px; background: rgba(0,0,0,.78); color: #fff; }
.chip {
  left: 10px; top: 10px;
  background: var(--accent); color: #04070d;
  letter-spacing: .03em;
}

.meta { padding: 14px 16px 16px; }
.name {
  font-size: 16px; font-weight: 550; line-height: 1.35;
  word-break: break-word;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.sub {
  margin-top: 6px; font-size: 13.5px; color: var(--dim);
  font-variant-numeric: tabular-nums;
}

/* ---- empty ----------------------------------------------------------- */

.empty { padding: 90px 24px; text-align: center; color: var(--dim); }
.empty svg { width: 46px; height: 46px; opacity: .3; margin-bottom: 15px; }
.empty p { margin: 0; font-size: 16px; }
.empty .hint { margin-top: 7px; font-size: 14px; opacity: .7; }

/* A headset is always wide; this is for a phone checking the library. */
@media (max-width: 720px) {
  .wrap { display: block; }
  aside {
    position: static; width: auto; height: auto;
    border-right: 0; border-bottom: 1px solid var(--line);
    max-height: 42vh;
  }
  header, main { padding-left: 18px; padding-right: 18px; }
  .grid { grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); }
  h1 { font-size: 22px; }
}

@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; }
}
"""

# Inline so the page stays self-contained; no icon font, no network fetch.
_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round">%s'
        '</svg>')
ICON_FOLDER = _SVG % ('<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17'
                      'a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>')
ICON_HOME = _SVG % ('<path d="M3 10.5 12 4l9 6.5V20a1 1 0 0 1-1 1H4a1 1 0 0 '
                    '1-1-1z"/>')
ICON_FILM = _SVG % ('<rect x="3" y="5" width="18" height="14" rx="2"/>'
                    '<path d="M7 5v14M17 5v14M3 12h18M3 8.5h4M3 15.5h4'
                    'M17 8.5h4M17 15.5h4"/>')

PAGE = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<meta name='color-scheme' content='dark'>"
    "<title>%s</title><style>%s</style></head><body><div class='wrap'>"
    "<aside><div class='brand'>%s</div><nav class='tree'>%s</nav></aside>"
    "<section><header>%s<h1>%s</h1><div class='count'>%s</div></header>"
    "<main>%s</main></section></div></body></html>")


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


def subdirs(path, at_root):
    """Just the folders, for the rail."""
    try:
        names = visible_names(path, at_root)
    except OSError:
        return []
    return [n for n in names if os.path.isdir(os.path.join(path, n))]


def videos(path, at_root):
    """Just the playable files, for the grid."""
    names = visible_names(path, at_root)
    return [n for n in names if not os.path.isdir(os.path.join(path, n))]


def tree(root, here):
    """The rail.

    Only the branch leading to the current folder is opened, plus that
    folder's own children. Walking the whole library to draw a fully expanded
    tree would cost a recursive scan on every page view; this costs one
    listdir per level of depth.
    """
    rel = "" if here == root else os.path.relpath(here, root)
    trail = rel.split(os.sep) if rel else []

    def level(path, depth, url):
        out = []
        for name in subdirs(path, at_root=(depth == 0)):
            child = os.path.join(path, name)
            curl = "%s/%s" % (url, urllib.parse.quote(name))
            on_trail = depth < len(trail) and trail[depth] == name
            current = on_trail and depth == len(trail) - 1
            cls = "node on" if current else ("node open" if on_trail
                                             else "node")
            out.append("<a class='%s' href='%s/'>%s<span>%s</span></a>"
                       % (cls, curl, ICON_FOLDER, html.escape(name)))
            if on_trail:
                inner = level(child, depth + 1, curl)
                if inner:
                    out.append("<div class='branch'>%s</div>" % inner)
        return "".join(out)

    home_cls = "node on" if not trail else "node open"
    home = ("<a class='%s' href='/'>%s<span>%s</span></a>"
            % (home_cls, ICON_HOME,
               html.escape(os.path.basename(root) or "Library")))
    return home + level(root, 0, "")


def breadcrumbs(rel_here, home):
    """Every ancestor as its own link, so a deep tree is one tap to escape."""
    if not rel_here:
        return "<div class='crumbs'><span class='here'>%s</span></div>" % home

    parts = rel_here.split(os.sep)
    out = ["<a href='/'>%s</a>" % home]
    trail = ""
    for i, part in enumerate(parts):
        trail = "%s/%s" % (trail, urllib.parse.quote(part))
        out.append("<span class='sep'>/</span>")
        if i == len(parts) - 1:
            out.append("<span class='here'>%s</span>" % html.escape(part))
        else:
            out.append("<a href='%s/'>%s</a>" % (trail, html.escape(part)))
    return "<div class='crumbs'>%s</div>" % "".join(out)


def video_card(full, link, safe_name, root):
    """A card carrying the thumbnail, duration, size and a 3D marker."""
    rel = os.path.relpath(full, root)
    key = cache_key(full)
    meta = probe(full, key) if key else {}

    duration = human_time(meta.get("duration"))
    chip = "<span class='chip'>3D SBS</span>" if is_sbs(meta) else ""
    dur = "<span class='dur'>%s</span>" % duration if duration else ""
    try:
        size = human_size(os.path.getsize(full))
    except OSError:
        size = ""
    if meta.get("width"):
        size += " &middot; %d&times;%d" % (meta["width"], meta["height"])

    return ("<a class='card' href='%s'>"
            "<div class='shot'><div class='glyph'>%s</div>"
            "<img loading='lazy' src='%s%s' alt=''>"
            "<div class='scrim'></div>%s%s</div>"
            "<div class='meta'><div class='name'>%s</div>"
            "<div class='sub'>%s</div></div></a>"
            % (link, ICON_FILM, THUMB_PREFIX, urllib.parse.quote(rel),
               chip, dur, safe_name, size or "&nbsp;"))


def render(path):
    """The full HTML page for a directory. Raises OSError if unreadable."""
    root = os.path.realpath(ROOT)
    here = os.path.realpath(path)
    rel_here = "" if here == root else os.path.relpath(here, root)
    at_root = (here == root)

    home = os.path.basename(root) or "Library"
    title = rel_here.split(os.sep)[-1] if rel_here else home

    names = videos(path, at_root)
    cards, total = [], 0
    for name in names:
        full = os.path.join(path, name)
        try:
            total += os.path.getsize(full)
        except OSError:
            pass
        cards.append(video_card(full, urllib.parse.quote(name),
                                html.escape(name), root))

    if cards:
        count = "%d video%s &middot; %s" % (len(cards),
                                            "" if len(cards) == 1 else "s",
                                            human_size(total))
        body = "<div class='grid'>%s</div>" % "".join(cards)
    else:
        count = "No video here"
        deeper = subdirs(path, at_root)
        hint = ("<p class='hint'>Pick a folder from the tree.</p>"
                if deeper else "")
        body = ("<div class='empty'>%s<p>Nothing to play in this folder.</p>"
                "%s</div>" % (ICON_FILM, hint))

    return PAGE % (html.escape(title), CSS, html.escape(home),
                   tree(root, here),
                   breadcrumbs(rel_here, html.escape(home)),
                   html.escape(title), count, body)
