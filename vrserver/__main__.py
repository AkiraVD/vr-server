"""Entry point.

    python3 -m vrserver [-appname TAG] [--print-config] [--print-paths]
    python3 -m vrserver --set-password [USER]
"""

import getpass
import json
import os
import shutil
import sys
import threading

from .access import (ALLOW_NETS, auth_configured, hash_password, log_line,
                     protect_roots)
from .config import APPNAME, CFG, CONFIG_PATH, HOST, LOG_PATH, PORT, ROOT
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
    roots = protect_roots()
    if roots:
        names = ", ".join(os.path.relpath(r, ROOT) for r in roots)
        if auth_configured():
            log_line("protected (password required): %s" % names)
        else:
            # Loud, because these folders are now unreachable rather than open.
            log_line("WARNING: protected folders but no password set, "
                     "refusing them entirely: %s" % names)
            log_line("         set one with: python3 -m vrserver --set-password")
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


def set_password(argv):
    """Prompt for a password and store only its hash in config.json.

    A separate command rather than a config key you type by hand: the plain
    password should never sit in the file, and nothing else needs to know it.
    """
    user = ""
    if "--set-password" in argv:
        i = argv.index("--set-password")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            user = argv[i + 1]
    user = user or input("username: ").strip()
    if not user:
        print("a username is required", file=sys.stderr)
        return 1

    password = getpass.getpass("password: ")
    if len(password) < 8:
        print("use at least 8 characters", file=sys.stderr)
        return 1
    if password != getpass.getpass("again: "):
        print("they do not match", file=sys.stderr)
        return 1

    try:
        with open(CONFIG_PATH) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    except FileNotFoundError:
        data = {}
    except Exception as exc:
        print("cannot read %s (%s)" % (CONFIG_PATH, exc), file=sys.stderr)
        return 1

    data["auth_user"] = user
    data["auth_hash"] = hash_password(password)

    # Written 0600 before any content lands in it: the hash is not a password,
    # but it is still the only thing standing in front of the protected folders.
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)

    print("stored for %r in %s" % (user, CONFIG_PATH))
    if not data.get("protect"):
        print("note: `protect` is empty, so nothing requires it yet")
    print("restart the server for this to take effect")
    return 0


def cli(argv):
    if "--set-password" in argv:
        return set_password(argv)
    if "--print-config" in argv:
        shown = dict(CFG)
        if shown.get("auth_hash"):
            shown["auth_hash"] = "<set>"
        print(json.dumps(shown, indent=2, sort_keys=True))
        return 0
    if "--print-paths" in argv:
        # Machine-readable for start.sh: root on line 1, port on line 2.
        print(ROOT)
        print(PORT)
        return 0
    return main()


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
