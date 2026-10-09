# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An almost read-only HTTP server (Python 3 standard library + ffmpeg, no
packages) that serves a VR video library to a player inside a Quest 3 headset.
Nothing is transcoded. Serving is GET and HEAD only.

The one exception is `POST /_protect`, which rewrites the `protect` list in
`config.json` and nothing else. It exists so the locked folders can be chosen
from the headset, needs the unlock cookie, and is the only write the HTTP layer
performs. Keep it that way: new writes do not belong here.

## Commands

    ./start.sh                           run (foreground; stops any old server first)
    ./run-tests.sh                       full suite (121 tests, unittest, no pytest)
    ./run-tests.sh -k test_suffix_range  a single test (args pass through to unittest)
    python3 -m unittest tests.test_http -v          one module
    python3 -m vrserver --print-config   resolved settings as JSON
    python3 -m vrserver --print-paths    root on line 1, port on line 2 (start.sh parses this)

There is no linter, formatter, or build step configured.

## Architecture

Modules form a strict one-way dependency chain; keep it that way:

    config -> access -> media -> listing -> server -> __main__

* `config.py` — resolves settings **at import time**: `config.json`, then
  `VRSERVE_*` environment overrides, then type coercion. Exposes the result as
  module-level constants (`ROOT`, `PORT`, `THUMB_DIR`, `CFG`, ...). Loading is
  never fatal: bad config warns to stderr and falls back to `DEFAULTS`,
  because an unreadable error inside a headset is worse than a running server.
* `access.py` — the access log, the IP allow list (`ip_allowed`), the scan
  scope (`in_scope` / `scan_roots`, memoized), and the protect gate
  (`is_protected`, `credentials_ok`, `make_cookie`, `set_protect`).
* `media.py` — `ffprobe` metadata and `ffmpeg` thumbnails, plus `warm_cache`,
  the startup render pass run on a daemon thread from `__main__`.
* `listing.py` — the dark, large-tap-target directory page. Pure HTML/CSS
  string building; no template engine.
* `server.py` — `SimpleHTTPRequestHandler` with `send_head` replaced so it can
  answer `Range` with 206, plus the `/_thumb/` route and path/IP gates.

### Import-time configuration is the main gotcha

Because `config.py` reads everything on import, settings cannot be changed
after `import vrserver`. Tests therefore **must** import `tests/fixture.py`
first — it builds a temporary media tree, writes a config, sets
`VRSERVE_CONFIG`, and only then imports the package, re-exporting the modules.
Always reach the package through `from .fixture import ... , server, config`,
never `import vrserver` directly in a test.

### Three gates; two fail open, one fails closed

Every request passes `ip_allowed(peer)` (allow list, plain IPs or CIDR) and
then `in_scope(path)` (must be `root` itself or inside a configured `scan`
folder). An empty `allow` or `scan` list disables that check entirely rather
than denying everything — deliberate, and asserted by tests. `safe_abs` /
`translate_path` additionally realpath-confine everything to `ROOT`.

`protect` is the third gate and the deliberate exception. A folder listed there
needs the unlock cookie before it is served, listed, or thumbnailed, and
one with **no usable password is refused outright** rather than served: the
fail-open argument does not survive a gate whose failure would publish exactly
what it exists to hide. `tests/test_protect.py` asserts that inversion, and
`find_videos` skips protected folders so the startup pass never renders a
thumbnail of content nobody has unlocked.

`/_unlock` serves a password form and, on POST, sets an HMAC-signed cookie (key
at `.unlock.key`, 0600, git-ignored); `/_lock` clears it. The cookie is the
*only* thing `authed()` accepts.

Deliberately not HTTP Basic, which an earlier version used. A browser that
answers a Basic challenge keeps sending those credentials to the origin for the
rest of the session with no way to revoke them, so `/_lock` cleared the cookie
and the next request arrived authenticated regardless. Do not reintroduce an
`Authorization` path; `tests/test_protect.py` asserts that such a header grants
nothing.

### Range support is the point of the project

`send_file` parses `bytes=a-b`, `bytes=a-`, and suffix `bytes=-n`, clamps the
end to EOF, returns 416 with `Content-Range: bytes */size` on anything
unsatisfiable or malformed, and hands back a `Slice` wrapper that stops
`copyfileobj` after exactly `length` bytes. `BrokenPipeError` /
`ConnectionResetError` in `do_GET`/`do_HEAD` is normal — players abort
mid-transfer on every seek. Test Range changes against the live server in
`tests/test_http.py`, not by inspecting `RANGE_RE`.

### Thumbnail cache

Key is `sha1(realpath|st_mtime_ns|st_size)`; `<key>.jpg` and `<key>.json`
(ffprobe metadata) live side by side in `THUMB_DIR`, so replacing a file
invalidates both. VR sources are full side-by-side, so when
`width/height >= SBS_MIN_ASPECT` (1.9) only the left eye is cropped before
scaling. Renders go through a global `BoundedSemaphore(MAX_FFMPEG)` plus a
per-key lock, so the startup pass and on-demand requests together never exceed
`max_ffmpeg` ffmpeg processes and never render the same thumb twice. A seek
past the last frame produces nothing silently, so `make_thumb` retries at 0.0.

### The Play button hands off to the device's own player

There is deliberately no in-browser player: browsers cannot decode HEVC or
E-AC-3 audio, and fixing that means converting files. Each card instead carries
a **Play** button that starts VLC (or whatever the device uses) on the raw
stream, which handles every codec and all audio/subtitle tracks. The card body
still links to the raw file for headsets. `player_href` in `listing.py` picks
the link by user-agent: Android gets an `intent://` that launches VLC (falling
back to the playlist), iOS `vlc://`, desktop `vrplay:?url=<encoded>`, and the
Quest browser gets no button. Desktop VLC registers no link scheme, so
`tools/install-player-handler.sh` registers `vrplay:` once per machine and
`tools/vrplay_handler.py` launches the first of vlc/celluloid/mpv. That handler
is reachable from any web page, so it refuses every address outside loopback,
LAN and tailnet (`tests/test_handler.py`); keep that check. With
`"desktop_scheme": false` desktop uses the `/_m3u/<path>` playlist instead, and
`"player_button": false` removes the button and the route.

The player fetches the file itself, so it cannot send the unlock cookie:
protected files get no button and `/_m3u/` refuses them. The URL handed over is
built from the request's `Host` header (the only address known to be
reachable), validated by `HOST_RE` because it lands in a link and a playlist.

### The instance tag

`APPNAME` is passed as `-appname TAG` in argv so `start.sh` can find a running
server by scanning `/proc/*/cmdline` (exact argv element matches, never
substrings) as well as by port. It is intentionally **not** a `config.json`
key: editing it there would orphan the very process the tag exists to find.

## Conventions

Module and function docstrings explain *why* a decision was made, not what the
code does; comments mark the non-obvious (fail-open choices, retry reasons,
`exec` in `start.sh`). Match that density. No third-party imports — stdlib
only, on both sides of the test boundary.

`config.json` is git-ignored — it names real media paths and LAN device
addresses. `config.example.json` is the tracked template; keep the two in sync
when adding a key, and add the key to `DEFAULTS` in `config.py` and the table
in `README.md` as well.
