"""Entry point.

    python3 -m vrserver [-appname TAG] [--print-config] [--print-paths]
"""

import json
import os
import shutil
import sys
import threading

from .access import ALLOW_NETS, log_line
from .config import APPNAME, CFG, HOST, LOG_PATH, PORT, ROOT
from .media import warm_cache
from .server import create_server


def announce():
    """State the policy in force, so a surprise is visible at startup."""
    if ALLOW_NETS:
        log_line("allow list: %s" % ", ".join(str(n) for n in ALLOW_NETS))
    else:
        log_line("WARNING: no allow list - any host on the LAN can connect")
    if LOG_PATH:
        log_line("access log: %s" % LOG_PATH)
    log_line("appname: %s" % APPNAME)
    log_line("serving %s on http://%s:%d/" % (ROOT, HOST, PORT))


def main():
    if not os.path.isdir(ROOT):
        print("root not available: %s" % ROOT, file=sys.stderr)
        return 1

    if not shutil.which("ffmpeg"):
        print("warning: ffmpeg not found - thumbnails disabled", file=sys.stderr)
    else:
        # In the background: serving must not wait on a full render pass.
        threading.Thread(target=warm_cache, daemon=True).start()

    server = create_server()
    announce()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cli(argv):
    if "--print-config" in argv:
        print(json.dumps(CFG, indent=2, sort_keys=True))
        return 0
    if "--print-paths" in argv:
        # Machine-readable for start.sh: root on line 1, port on line 2.
        print(ROOT)
        print(PORT)
        return 0
    return main()


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
