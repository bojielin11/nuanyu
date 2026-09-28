"""Run only the desktop device/network bridge for diagnostics."""

import time

from desktop.nuanyu_desktop import DesktopBridge


bridge = DesktopBridge()
bridge.start()
while True:
    time.sleep(10)
