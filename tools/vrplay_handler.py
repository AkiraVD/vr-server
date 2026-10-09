#!/usr/bin/env python3
"""Open a vrplay: link in whichever video player is installed.

Desktop VLC registers no link scheme of its own, so a button on a web page has
no way to start it. The browser can hand a vrplay: link to the OS, though, and
this is what the OS runs. install-player-handler.sh registers it.

Any web page can fire such a link, so the address is checked before anything
is launched: only this machine, the LAN and the tailnet are accepted. Without
that, a hostile page could make a local player open a stream of its choosing.
"""

import ipaddress
import os
import shutil
import subprocess
import sys
import urllib.parse

# First one found wins; VRPLAY_PLAYER=name overrides.
PLAYERS = ("vlc", "celluloid", "mpv")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
LOCAL_SUFFIXES = (".ts.net", ".local", ".lan", ".home.arpa")


def trusted_host(host):
    """This machine, the LAN, or the tailnet."""
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local \
        or addr in TAILNET


def stream_url(link):
    """The http(s) URL inside a vrplay:?url=... link, or None if not allowed."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    url = (query.get("url") or [""])[0]
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if not trusted_host(parts.hostname):
        return None
    return url


def pick_player(env=None):
    """Path of the player to run, or None."""
    wanted = (env if env is not None else os.environ).get("VRPLAY_PLAYER")
    for name in ((wanted,) if wanted else PLAYERS):
        path = shutil.which(name)
        if path:
            return path
    return None


def main(argv):
    if len(argv) != 2:
        print("usage: vrplay_handler.py vrplay:?url=<encoded http url>",
              file=sys.stderr)
        return 2
    url = stream_url(argv[1])
    if not url:
        print("refusing: not an http(s) address on this machine, LAN or "
              "tailnet", file=sys.stderr)
        return 2
    player = pick_player()
    if not player:
        print("no player found; install vlc, celluloid or mpv",
              file=sys.stderr)
        return 3
    # No shell, and the URL is known to start with http, so it cannot be read
    # as an option.
    subprocess.Popen([player, url], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
