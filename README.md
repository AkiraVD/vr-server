# vr-server

A small read-only HTTP server for watching a VR video library on a Quest 3.

Files are streamed straight to a player in the headset (DeoVR, HereSphere) at
full quality. Nothing is transcoded, so a 5.8K HEVC file arrives as itself.

## Setup

`config.json` is not in the repository -- it names your folder layout and the
addresses of devices on your network, so it is ignored by git. Create your own
from the tracked example:

    cp config.example.json config.json

Then edit at least `root` to point at your media folder. Every key is optional:
anything you leave out falls back to the built-in default, and a missing or
malformed config warns on stderr and starts with defaults rather than refusing
to run. Check what the server actually resolved with:

    python3 -m vrserver --print-config

## Running

    ./start.sh

Runs in the foreground; Ctrl+C stops it. Any server already running is stopped
first. Then open the printed address in the headset's browser.

Nothing starts automatically, and nothing is installed: the only requirements
are Python 3 and ffmpeg.

## Layout

    config.example.json  tracked template; copy it to config.json
    config.json          your settings, git-ignored; the file you normally edit
    start.sh             launcher: stops the old server, runs the new one
    access.log           request log (created on first run)
    .thumbs/             thumbnail and metadata cache (created on first run)
    run-tests.sh         test runner
    tests/               test suite
    vrserver/
        config.py      defaults, config.json, environment overrides, appname
        access.py      access log, IP allow list, which paths are servable
        media.py       ffprobe metadata, ffmpeg thumbnails, startup pass
        listing.py     the directory page: CSS and the HTML grid
        server.py      HTTP layer: handler, Range support, routes
        __main__.py    entry point and command line

## Tests

    ./run-tests.sh

57 tests, standard library only -- no pytest, nothing to install. They run
against a temporary media tree, so your real library, cache and config are
never touched.

    tests/fixture.py       temporary tree + config; import this before vrserver
    tests/test_http.py     Range handling and routing, against a live server
    tests/test_access.py   IP allow list and scan scope
    tests/test_media.py    side-by-side detection, cache-key invalidation
    tests/test_listing.py  what the page shows, and formatting

The HTTP tests run a real server on an ephemeral port rather than inspecting
the regex, because Range handling is the reason this project exists and its
edge cases (suffix ranges, ends past EOF, malformed headers) are exactly where
mistakes hide.

## config.json

| key | default | meaning |
| --- | --- | --- |
| `root` | `media` beside this file | folder the server is rooted at; nothing outside is reachable. Set this |
| `host` | `0.0.0.0` | address to bind; `0.0.0.0` means every interface |
| `port` | `8080` | TCP port |
| `thumbs` | `.thumbs` beside this file | thumbnail cache directory |
| `log` | `""` | access log path; empty means console only |
| `scan` | `[]` | folders shown at the root and pre-rendered on startup, walked recursively. Anything outside them returns 403. Empty disables the restriction |
| `allow` | `[]` | IPs or CIDR ranges allowed to connect. Empty allows everyone, with a warning |
| `thumb_width` | `480` | thumbnail width in pixels |
| `seek_fraction` | `0.5` | where in the video to grab the frame; `0.5` is the middle |
| `max_ffmpeg` | `3` | concurrent ffmpeg processes |

`scan` entries are relative to `root` (absolute paths work too, but one
outside `root` is skipped with a warning). `thumbs` and `log` are relative to
the directory you run from, which `start.sh` always makes the project root.
Unknown keys are ignored with a warning, so a typo is visible rather than
silent.

Four settings can also be overridden from the environment, which wins over the
file: `VRSERVE_ROOT`, `VRSERVE_HOST`, `VRSERVE_PORT`, `VRSERVE_THUMBS`.
`VRSERVE_CONFIG` points at a different config file altogether.

Both `scan` and `allow` fail open when empty. That is deliberate: this server
is driven from inside a headset, where a config typo that silently denied
everything would be very hard to diagnose.

The instance tag lives in `start.sh`, not here, because it is what finds an
already-running server. If it were editable in the config, changing it would
orphan the running process it exists to find.

## Command line

    python3 -m vrserver                  run
    python3 -m vrserver --print-config   resolved settings as JSON
    python3 -m vrserver --print-paths    root and port, for start.sh
    python3 -m vrserver -appname TAG     override the instance tag

## Notes

* **Range requests.** Python's `http.server` ignores the `Range` header, so a
  player cannot seek and many refuse to start at all. `server.py` answers 206
  properly; this is the single most important thing here.
* **Thumbnails.** VR video is full side-by-side, both eyes in one 2:1 frame, so
  a plain frame grab is two squashed copies of the picture. For ~2:1 sources
  only the left eye is cropped. Cached by path, mtime and size, so replacing a
  file invalidates its thumbnail.
* **Listings** show folders and playable video only. Subtitles and cover art
  cannot be opened by a VR player and would only be clutter.
* **No authentication.** The allow list is the only access control, so keep
  this on a trusted network and do not expose the port to the internet.
