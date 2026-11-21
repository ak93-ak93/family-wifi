"""Check that Family WiFi can control your UniFi console.

    python -m family_wifi.check                    # read-only checks
    python -m family_wifi.check --usage            # also show data used today and top apps
    python -m family_wifi.check --try-pause "Alex iPad"   # also pause one device for 60 s

Docker:  docker compose run --rm family-wifi python -m family_wifi.check
Your API key and password are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from .config import load_config
from .unifi import UnifiClient, UnifiError
from .usage import UsageTracker, human_bytes

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("--try-pause", metavar="DEVICE", help="pause this device or group for --seconds, then restore it")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--usage", action="store_true", help="also show online status, data used today and top apps")
    ap.add_argument("--show-rules", action="store_true", help="print your existing traffic rules (JSON)")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} config: {e}")
        return 1
    u = cfg.unifi
    print(f"Console: {u.url}   site: {u.site}")
    print(f"Auth:    {'API key' if u.api_key else 'no API key'}"
          f"{' + username/password fallback' if u.has_login and u.api_key else (' username/password' if u.has_login else '')}\n")

    client = UnifiClient(u)
    problems = 0

    def step(name, fn):
        nonlocal problems
        try:
            result = fn()
            print(f"{OK} {name}{': ' + result if result else ''}")
            return True
        except UnifiError as e:
            print(f"{FAIL} {name}: {e}")
        except httpx.HTTPError as e:
            print(f"{FAIL} {name}: can't reach the console ({e.__class__.__name__}: {e})")
        problems += 1
        return False

    if not step("Reach the console", lambda: (client._detect(), "UniFi OS" if client.is_unifi_os else "standalone Network app")[1]):
        return 1
    if not step("Sign in", lambda: (client.connect(), client.auth_mode.replace("_", " "))[1]):
        return 1

    def clients():
        online = client.online_clients()
        known = {c.get("mac") for c in client.all_clients()}
        missing = [f"{name} ({mac})" for mac, name in
                   ((m, n) for n, ms in cfg.devices.items() for m in ms) if mac not in known]
        msg = f"{len(online)} online, {len(known)} known"
        if missing:
            msg += f"\n   {WARN} UniFi has never seen: " + ", ".join(missing) + " (check the MACs / Private Wi-Fi Address)"
        return msg

    step("Read clients (needed for everything)", clients)
    step("Read bandwidth profiles (needed for Slow down)",
         lambda: ", ".join(g.get("name", "?") for g in client.usergroups()) or "none yet")

    rules: list = []

    def traffic_rules():
        rules.extend(client.request("GET", "/trafficrules", v2=True) or [])
        domain_rules = [r for r in rules if r.get("matching_target") == "DOMAIN"]
        msg = f"{len(rules)} rules"
        if domain_rules:
            sample = domain_rules[0].get("domains")
            msg += f"; domain format on your console: {json.dumps(sample)[:120]}"
        return msg

    step("Read traffic rules (needed for Block websites)", traffic_rules)
    if args.show_rules:
        print(json.dumps(rules, indent=2))

    # Harmless write check: block + unblock a made-up MAC that's not on your network.
    def write_check():
        dummy = "02:00:00:fa:fa:01"
        try:
            client.block_client(dummy)
        except UnifiError as e:
            if "UnknownStation" not in str(e):
                raise
            return "allowed (tested with a made-up device)"
        client.unblock_client(dummy)
        return "allowed (tested with a made-up device)"

    step("Permission to block devices (needed for Pause)", write_check)

    def usage():
        from datetime import datetime
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from zoneinfo import ZoneInfo

        macs = sorted({m for ms in cfg.devices.values() for m in ms})
        if not macs:
            return "no devices in config"
        with TemporaryDirectory() as tmp:
            tracker = UsageTracker(client, str(Path(tmp) / "usage.json"))
            now = datetime.now(ZoneInfo(cfg.timezone))
            data = tracker.poll(macs, now)
            # DPI only gives running totals, so show the totals once for a sanity check.
            rows = client.request("POST", "/stat/stadpi", {"type": "by_app", "macs": macs}) if tracker.dpi_available else []
        lines = []
        for name, ms in cfg.devices.items():
            u = tracker.for_macs(ms)
            lines.append(f"   {name}: {'online' if u.online else 'offline'}, today {human_bytes(u.today_bytes)}")
        if tracker.reports_available is False:
            lines.append(f"   {WARN} usage reports not available, so 'Data used today' won't work")
        if tracker.dpi_available is False:
            lines.append(f"   {WARN} traffic identification is off: turn it on in UniFi for 'Top app today'")
        else:
            from .usage import app_name
            for row in rows or []:
                apps = sorted(row.get("by_app") or [], key=lambda a: -(int(a.get("rx_bytes") or 0) + int(a.get("tx_bytes") or 0)))[:3]
                if apps:
                    names = ", ".join(app_name(int(a.get("cat", 0)), int(a.get("app", 0))) for a in apps)
                    lines.append(f"   {row.get('mac')}: top apps (all time) {names}")
        return "\n" + "\n".join(lines)

    if args.usage:
        step("Usage (online, data today, apps)", usage)

    if args.try_pause:
        macs = cfg.resolve(args.try_pause)
        print(f"\nPausing {args.try_pause} for {args.seconds} s ...")
        try:
            for mac in macs:
                client.block_client(mac)
            print(f"{OK} Paused. Check the device: the internet should stop within a few seconds.")
            time.sleep(args.seconds)
        finally:
            for mac in macs:
                client.unblock_client(mac)
            print(f"{OK} Restored {args.try_pause}.")

    print("\nAll good." if not problems else f"\n{problems} check(s) failed. See above.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
