#!/usr/bin/env bash
# Install mouse-gimbal for the current user. Asks for root once (udev rule).
set -euo pipefail
here="$(dirname "$(readlink -f "$0")")"

mkdir -p ~/.local/bin ~/.local/share/applications
ln -sf "$here/mouse-gimbal" ~/.local/bin/mouse-gimbal
cat > ~/.local/share/applications/mouse-gimbal.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Mouse Gimbal
Comment=Smooth cinematic mouse camera for games
Exec=$HOME/.local/bin/mouse-gimbal
Icon=camera-video
Categories=Utility;Game;
DESK

rule=/etc/udev/rules.d/71-mouse-gimbal.rules
if ! cmp -s "$here/udev/71-mouse-gimbal.rules" "$rule" 2>/dev/null; then
    echo "Installing udev rule (root needed)..."
    pkexec sh -c "install -m644 '$here/udev/71-mouse-gimbal.rules' '$rule' && udevadm control --reload && udevadm trigger --subsystem-match=input --action=change"
fi
echo "Done. Run: mouse-gimbal   (mouse list: mouse-gimbal --list)"
