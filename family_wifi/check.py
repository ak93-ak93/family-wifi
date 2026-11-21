"""Check that Family WiFi can control your UniFi console.

    python -m family_wifi.check                    # read-only checks
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

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("--try-pause", metavar="DEVICE", help="pause this device or group for --seconds, then restore it")
    ap.add_argument("--seconds", type=int, default=60)
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
