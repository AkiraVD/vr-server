"""Read-only HTTP media server for browsing a VR video library in a headset.

Layout:
    config.py    settings: defaults, config.json, environment, appname
    access.py    access log, IP allow list, which paths are servable
    media.py     ffprobe metadata, ffmpeg thumbnails, startup render pass
    listing.py   the directory page: CSS and the HTML grid
    server.py    HTTP layer: request handler, Range support, routes
    __main__.py  entry point and command line

Run it with `python3 -m vrserver`, or via start.sh in the project root.
"""

__version__ = "1.0.0"
