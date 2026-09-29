"""Who may connect, what they may reach, and the record of both.

Three related jobs:
  * the access log, written to console and optionally to a file
  * the IP allow list, so a stray device on the LAN cannot wander in
  * the scan scope, so only configured folders are servable at all

Both restrictions fail open when unconfigured. That is deliberate: this server
is normally driven from inside a headset, and a config typo that silently
denied everything would be very hard to diagnose from there.
"""

import datetime
import ipaddress
import os
import sys
import threading

from .config import CFG, LOG_PATH, ROOT

_LOG_LOCK = threading.Lock()


def log_line(msg):
    """Timestamped line to the console, and to the access log if configured."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s  %s" % (stamp, msg)
    with _LOG_LOCK:
        print(line, flush=True)
        if LOG_PATH:
            try:
                with open(LOG_PATH, "a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


def parse_allow(entries):
    """Accepts plain addresses and CIDR ranges alike."""
    nets = []
    for entry in entries:
        try:
            nets.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            print("allow: ignoring invalid entry %r" % entry, file=sys.stderr)
    return nets


ALLOW_NETS = parse_allow(CFG["allow"])


def ip_allowed(addr):
    """No list configured means no restriction, so a typo cannot lock you out."""
    if not ALLOW_NETS:
        return True
    try:
        ip = ipaddress.ip_address(addr.replace("::ffff:", ""))
    except ValueError:
        return False
    return any(ip in net for net in ALLOW_NETS)


_SCAN_ROOTS = None


def scan_roots():
    """Configured folders, resolved and constrained to ROOT. Computed once."""
    global _SCAN_ROOTS
    if _SCAN_ROOTS is not None:
        return _SCAN_ROOTS
    root = os.path.realpath(ROOT)
    out = []
    for entry in CFG["scan"]:
        target = os.path.realpath(
            entry if os.path.isabs(entry) else os.path.join(root, entry))
        if target != root and not target.startswith(root + os.sep):
            print("scan: skipping %r - outside root" % entry, file=sys.stderr)
            continue
        if not os.path.isdir(target):
            print("scan: skipping %r - not a directory" % entry, file=sys.stderr)
            continue
        out.append(target)
    _SCAN_ROOTS = out
    return out


def in_scope(path):
    """Servable? The root itself, or anything inside a configured folder."""
    if not CFG["scan"]:
        return True
    target = os.path.realpath(path)
    if target == os.path.realpath(ROOT):
        return True
    return any(target == base or target.startswith(base + os.sep)
               for base in scan_roots())
