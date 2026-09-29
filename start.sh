#!/usr/bin/env bash
# Start the VR/media HTTP server for the Quest 3.
# Stops any server already running, then runs in the foreground:
# Ctrl+C (or closing this terminal) stops it. Nothing is left in the background.
# Settings live in config.json next to this script.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE="vrserver"

# Fixed tag used to find an already-running server. Deliberately NOT in
# config.json: changing it would leave the running server wearing the old tag,
# unfindable, which is the very thing this is meant to catch. Do not edit.
APPNAME="vr-server"

if [[ ! -f "$HERE/$PACKAGE/__main__.py" ]]; then
    echo "error: package missing: $HERE/$PACKAGE" >&2
    exit 1
fi

# Run from the project root so `python3 -m vrserver` resolves the package.
cd "$HERE"

# Ask the server for its resolved settings, so the two cannot drift apart.
if ! { IFS= read -r MEDIA_ROOT; IFS= read -r PORT; } < <(python3 -m "$PACKAGE" --print-paths); then
    echo "error: could not read configuration" >&2
    exit 1
fi

# Find the old server two ways: whatever holds our port, and anything tagged
# with our appname. The appname search is what survives a port change in
# config.json, where a port-only lookup would miss the server still running on
# the previous port.
#
# argv elements are compared exactly, never as substrings, so an unrelated
# process that merely mentions this path or tag is never matched and killed.
find_old() {
    {
        ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true
        local pid args i base
        for dir in /proc/[0-9]*; do
            pid="${dir#/proc/}"
            [[ -r "$dir/cmdline" ]] || continue
            mapfile -d '' -t args < "$dir/cmdline" 2>/dev/null || continue
            (( ${#args[@]} >= 2 )) || continue
            base="${args[0]##*/}"
            [[ "$base" == python* ]] || continue
            for ((i = 0; i + 1 < ${#args[@]}; i++)); do
                if [[ "${args[i]}" == "-appname" || "${args[i]}" == "--appname" ]] \
                   && [[ "${args[i+1]}" == "$APPNAME" ]]; then
                    echo "$pid"
                    break
                fi
            done
        done
    } | sort -u | grep -vx "$$" || true
}

OLD="$(find_old)"
if [[ -n "$OLD" ]]; then
    echo "  stopping old server (pid $(echo "$OLD" | tr '\n' ' ' | sed 's/ $//'))"
    kill $OLD 2>/dev/null || true
    for _ in $(seq 1 40); do
        [[ -z "$(find_old)" ]] && break
        sleep 0.25
    done
    LEFT="$(find_old)"
    if [[ -n "$LEFT" ]]; then
        echo "  forcing (pid $(echo "$LEFT" | tr '\n' ' ' | sed 's/ $//'))"
        kill -9 $LEFT 2>/dev/null || true
        sleep 0.5
    fi
fi

if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
    echo "error: port $PORT is still in use by something else" >&2
    echo "       check with:  ss -ltnp | grep $PORT" >&2
    exit 1
fi

if [[ ! -d "$MEDIA_ROOT" ]]; then
    echo "error: media root not available: $MEDIA_ROOT" >&2
    echo "       check 'root' in $HERE/config.json, or whether its drive is" >&2
    echo "       mounted:  findmnt -T \"$MEDIA_ROOT\"" >&2
    exit 1
fi

# Report the address the headset should actually use, not a hardcoded one.
ip_addr="$(ip -4 -o route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || true)"
[[ -n "$ip_addr" ]] || ip_addr="<this-pc>"

echo
echo "  Appname : $APPNAME"
echo "  Serving : $MEDIA_ROOT"
echo "  Config  : $HERE/config.json"
echo "  Browse  : http://${ip_addr}:${PORT}/"
echo
echo "  Ctrl+C to stop."
echo

# exec: this shell becomes the server, so Ctrl+C reaches it directly and
# nothing survives this terminal.
exec python3 -m "$PACKAGE" -appname "$APPNAME"
