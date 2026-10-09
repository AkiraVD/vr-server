#!/bin/sh
# Register the vrplay: link scheme for the current user, so the Play button on
# the library page can start a desktop player. Run once per machine.
#
#   tools/install-player-handler.sh              install
#   tools/install-player-handler.sh uninstall    remove it again
set -eu

here=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}"
apps="$data/applications"
entry="$apps/vrplay.desktop"
# A copy, not a path into the repo: the machine you browse from is usually not
# the one the repo lives on, and only this folder has to be carried across.
home="$data/vrplay"

refresh() { update-desktop-database "$apps" 2>/dev/null || true; }

if [ "${1:-install}" = "uninstall" ]; then
    rm -f "$entry"
    rm -rf "$home"
    refresh
    echo "removed $entry and $home"
    exit 0
fi

mkdir -p "$apps" "$home"
cp "$here/vrplay_handler.py" "$home/vrplay_handler.py"
cat > "$home/vrplay-handler" <<'EOF'
#!/bin/sh
exec python3 "$(dirname "$(readlink -f "$0")")/vrplay_handler.py" "$@"
EOF
chmod +x "$home/vrplay-handler" "$home/vrplay_handler.py"
cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=VR server: open video in player
Exec="$home/vrplay-handler" %u
MimeType=x-scheme-handler/vrplay;
EOF
xdg-mime default vrplay.desktop x-scheme-handler/vrplay
refresh
echo "installed $entry"
echo "handler for vrplay: $(xdg-mime query default x-scheme-handler/vrplay)"
echo "The browser asks once before opening it; tick 'always allow'."
