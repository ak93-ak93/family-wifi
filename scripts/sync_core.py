"""Copy the shared UniFi logic into the Home Assistant integration.

The standalone server (family_wifi/) is the source of truth. Home Assistant
custom integrations must be self-contained, so the same modules are copied into
custom_components/family_wifi/core/. Run this after changing them; a test fails
if the copies drift apart.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ["config.py", "durations.py", "service.py", "state.py", "unifi.py"]

for name in SHARED:
    shutil.copyfile(ROOT / "family_wifi" / name, ROOT / "custom_components/family_wifi/core" / name)
    print("synced", name)
