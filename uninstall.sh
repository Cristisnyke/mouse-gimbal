#!/usr/bin/env bash
set -euo pipefail
rm -f ~/.local/bin/mouse-gimbal ~/.local/share/applications/mouse-gimbal.desktop
if [ -e /etc/udev/rules.d/71-mouse-gimbal.rules ]; then
    pkexec sh -c "rm -f /etc/udev/rules.d/71-mouse-gimbal.rules && udevadm control --reload && udevadm trigger --subsystem-match=input --action=change"
fi
echo "Removed. Config kept in ~/.config/mouse-gimbal"
