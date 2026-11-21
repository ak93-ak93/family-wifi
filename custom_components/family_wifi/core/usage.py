"""Per-device usage: online now, current speed, data used today, top apps today.

Sources (all UniFi):
- Online and speed: the live client list (stat/sta).
- Data used today: UniFi's own usage reports (hourly for earlier hours, 5-minute
  for the current hour), so it survives restarts and matches the UniFi app.
- Apps: traffic identification (DPI, stat/stadpi). UniFi only gives running
  totals, so today's numbers are worked out from the change between polls and
  reset at local midnight. Needs "Traffic Identification" on in UniFi.

UniFi doesn't record which websites (domains) a device visited.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .dpi_names import APPS, CATEGORIES
from .unifi import UnifiClient, UnifiError

log = logging.getLogger(__name__)


def app_name(cat: int, app: int) -> str:
    key = (cat << 16) + app
    if key in APPS:
        return APPS[key]
    return f"Other {CATEGORIES.get(cat, 'traffic')}".replace("_", " ")


@dataclass
class Usage:
    online: bool = False
    down_bps: float = 0  # bytes per second right now
    up_bps: float = 0
    today_bytes: int | None = None  # None = UniFi reports not available
    apps: dict[str, int] = field(default_factory=dict)  # app name -> bytes today

    def top_apps(self, n: int = 10) -> list[tuple[str, int]]:
        return sorted(((a, b) for a, b in self.apps.items() if b > 0), key=lambda x: -x[1])[:n]

    @classmethod
    def combine(cls, parts: list[Usage]) -> Usage:
        out = cls()
        for p in parts:
            out.online = out.online or p.online
            out.down_bps += p.down_bps
            out.up_bps += p.up_bps
            if p.today_bytes is not None:
                out.today_bytes = (out.today_bytes or 0) + p.today_bytes
            for a, b in p.apps.items():
                out.apps[a] = out.apps.get(a, 0) + b
        return out


class UsageTracker:
    def __init__(self, unifi: UnifiClient, path: str):
        self.unifi = unifi
        self.path = Path(path)
        self.lock = threading.Lock()
        # {"date": "YYYY-MM-DD", "last": {mac: {key: bytes}}, "today": {mac: {key: bytes}}}
        self.apps_state: dict = {"date": "", "last": {}, "today": {}}
        if self.path.exists():
            try:
                self.apps_state = json.loads(self.path.read_text())
            except ValueError:
                log.warning("Ignoring unreadable %s", self.path)
        self.devices: dict[str, Usage] = {}
        self.dpi_available: bool | None = None
        self.reports_available: bool | None = None

    def poll(self, macs: list[str], now: datetime) -> dict[str, Usage]:
        """Refresh usage for these MACs. `now` must be timezone aware (local time)."""
        with self.lock:
            out = {m: Usage() for m in macs}
            for c in self.unifi.online_clients():
                u = out.get(c.get("mac"))
                if u:
                    u.online = True
                    # UniFi counts from the network's side: tx = sent to the device.
                    u.down_bps = float(c.get("tx_bytes-r") or 0)
                    u.up_bps = float(c.get("rx_bytes-r") or 0)
            self._today_bytes(out, now)
            self._apps(out, now)
            self.devices = out
            return out

    def for_macs(self, macs) -> Usage:
        return Usage.combine([self.devices[m] for m in macs if m in self.devices])

    # ----- data used today -----

    def _report(self, interval: str, mac: str, start: datetime, end: datetime) -> list[dict]:
        return self.unifi.request("POST", f"/stat/report/{interval}.user", {
            "attrs": ["time", "rx_bytes", "tx_bytes"],
            "start": int(start.timestamp() * 1000),
            "end": int(end.timestamp() * 1000),
            "mac": mac,
            "macs": [mac],
        }) or []

    def _today_bytes(self, out: dict[str, Usage], now: datetime) -> None:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour = now.replace(minute=0, second=0, microsecond=0)
        hour_ms = int(hour.timestamp() * 1000)
        try:
            for mac, u in out.items():
                total = 0
                if hour > midnight:
                    for row in self._report("hourly", mac, midnight, hour - timedelta(seconds=1)):
                        if row.get("time", 0) < hour_ms and row.get("user", mac) == mac:
                            total += int(row.get("rx_bytes") or 0) + int(row.get("tx_bytes") or 0)
                for row in self._report("5minutes", mac, hour, now):
                    if row.get("time", 0) >= hour_ms and row.get("user", mac) == mac:
                        total += int(row.get("rx_bytes") or 0) + int(row.get("tx_bytes") or 0)
                u.today_bytes = total
            self.reports_available = True
        except UnifiError as e:
            if self.reports_available is not False:
                log.warning("UniFi usage reports unavailable: %s", e)
            self.reports_available = False

    # ----- apps today -----

    def _apps(self, out: dict[str, Usage], now: datetime) -> None:
        try:
            rows = self.unifi.request("POST", "/stat/stadpi", {"type": "by_app", "macs": list(out)}) or []
        except UnifiError as e:
            if self.dpi_available is not False:
                log.warning("UniFi traffic identification (DPI) unavailable: %s", e)
            self.dpi_available = False
            return
        self.dpi_available = True

        st = self.apps_state
        today = now.date().isoformat()
        if st.get("date") != today:
            st["date"], st["today"] = today, {}
        for row in rows:
            mac = row.get("mac")
            if mac not in out:
                continue
            last = st["last"].setdefault(mac, {})
            acc = st["today"].setdefault(mac, {})
            for a in row.get("by_app") or []:
                key = str((int(a.get("cat", 0)) << 16) + int(a.get("app", 0)))
                cur = int(a.get("rx_bytes") or 0) + int(a.get("tx_bytes") or 0)
                prev = last.get(key)
                if prev is not None:
                    acc[key] = acc.get(key, 0) + (cur - prev if cur >= prev else cur)
                last[key] = cur
        for mac, u in out.items():
            for key, b in st["today"].get(mac, {}).items():
                k = int(key)
                name = app_name(k >> 16, k & 0xFFFF)
                u.apps[name] = u.apps.get(name, 0) + b
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.apps_state))
        os.replace(tmp, self.path)


def human_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"
