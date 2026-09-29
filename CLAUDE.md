# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A read-only HTTP server (Python 3 standard library + ffmpeg, no packages) that
serves a VR video library to a player inside a Quest 3 headset. Nothing is
transcoded and nothing is written by the HTTP layer — GET and HEAD only.

## Commands

    ./start.sh                           run (foreground; stops any old server first)
    ./run-tests.sh                       full suite (57 tests, unittest, no pytest)
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
* `access.py` — the access log, the IP allow list (`ip_allowed`), and the scan
  scope (`in_scope` / `scan_roots`, memoized).
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

### Two independent gates, both fail open

Every request passes `ip_allowed(peer)` (allow list, plain IPs or CIDR) and
then `in_scope(path)` (must be `root` itself or inside a configured `scan`
folder). An empty `allow` or `scan` list disables that check entirely rather
than denying everything — deliberate, and asserted by tests. `safe_abs` /
`translate_path` additionally realpath-confine everything to `ROOT`.

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
