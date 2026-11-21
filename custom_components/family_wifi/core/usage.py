"""Per-device usage: online now, current speed, data used today, top apps today.

Sources (all UniFi):
- Online and speed: the live client list (stat/sta).
- Data used today: UniFi's own usage reports (hourly for earlier hours, 5-minute
  for the current hour), so it survives restarts and matches the UniFi app.
- Apps: traffic identification (DPI, stat/stadpi). UniFi only gives running
  totals, so today's numbers are worked out from the change between polls and
  reset at local midnight. Needs "Traffic Identification" on in UniFi.

Time estimates: UniFi doesn't measure screen time, so it's estimated from data
flow. A slice of time counts as "in use" when traffic between two polls stays
above a level typical for real use (video needs far more than chat). Slices are
merged across a kid's devices so two devices at once aren't counted twice.

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


# Bytes per second that mean someone is really using it (not just background sync).
DEVICE_ACTIVE_BPS = 3_000
CATEGORY_ACTIVE_BPS = {
    0: 2_000,  # instant messengers
    4: 25_000,  # media streaming: even low-quality video is ~40 kB/s
    6: 2_000,  # VoIP
    8: 2_000,  # online games: small but constant
    15: 2_000,  # web IM
    24: 5_000,  # social networks
}
DEFAULT_ACTIVE_BPS = 5_000
# Longest slice one poll can credit (covers UniFi updating DPI counters every few minutes).
MAX_SLICE = 6 * 60

Interval = tuple[float, float]


def merge(intervals) -> list[Interval]:
    out: list[list[float]] = []
    for a, b in sorted(tuple(x) for x in intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def seconds(intervals) -> float:
    return sum(b - a for a, b in merge(intervals))


def human_duration(secs: float | None) -> str:
    if not secs:
        return "0 min"
    mins = round(secs / 60)
    return f"{mins // 60} h {mins % 60:02d} min" if mins >= 60 else f"{mins} min"


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
    active: list[Interval] = field(default_factory=list)  # when the device was in use today
    app_active: dict[str, list[Interval]] = field(default_factory=dict)  # app -> when it was in use

    def top_apps(self, n: int = 10) -> list[tuple[str, int]]:
        return sorted(((a, b) for a, b in self.apps.items() if b > 0), key=lambda x: -x[1])[:n]

    @property
    def active_seconds(self) -> float:
        return seconds(self.active)

    def app_times(self, n: int = 10) -> list[tuple[str, float]]:
        """Apps by estimated time in use today, longest first."""
        times = ((a, seconds(iv)) for a, iv in self.app_active.items())
        return sorted(((a, t) for a, t in times if t > 0), key=lambda x: -x[1])[:n]

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
            out.active = merge(out.active + p.active)
            for a, iv in p.app_active.items():
                out.app_active[a] = merge(out.app_active.get(a, []) + iv)
        return out


class UsageTracker:
    def __init__(self, unifi: UnifiClient, path: str):
        self.unifi = unifi
        self.path = Path(path)
        self.lock = threading.Lock()
        # Running totals seen last time, when they last changed, and what they add up to today.
        self.apps_state: dict = {}
        if self.path.exists():
            try:
                self.apps_state = json.loads(self.path.read_text())
            except ValueError:
                log.warning("Ignoring unreadable %s", self.path)
        for k in ("last", "today", "app_change", "app_active", "dev_last", "dev_change", "active"):
            self.apps_state.setdefault(k, {})
        self.apps_state.setdefault("date", "")
        self.devices: dict[str, Usage] = {}
        self.dpi_available: bool | None = None
        self.reports_available: bool | None = None

    def poll(self, macs: list[str], now: datetime) -> dict[str, Usage]:
        """Refresh usage for these MACs. `now` must be timezone aware (local time)."""
        with self.lock:
            out = {m: Usage() for m in macs}
            st = self.apps_state
            today = now.date().isoformat()
            if st["date"] != today:  # new day: today's totals and times start from zero
                st["date"], st["today"], st["app_active"], st["active"] = today, {}, {}, {}
            ts = now.timestamp()
            for c in self.unifi.online_clients():
                mac = c.get("mac")
                u = out.get(mac)
                if u:
                    u.online = True
                    # UniFi counts from the network's side: tx = sent to the device.
                    u.down_bps = float(c.get("tx_bytes-r") or 0)
                    u.up_bps = float(c.get("rx_bytes-r") or 0)
                    total = int(c.get("tx_bytes") or 0) + int(c.get("rx_bytes") or 0)
                    _, slice_ = _step(st["dev_last"], st["dev_change"], mac, total, ts, DEVICE_ACTIVE_BPS)
                    if slice_:
                        st["active"][mac] = merge(st["active"].get(mac, []) + [slice_])
            for mac, u in out.items():
                u.active = [tuple(iv) for iv in st["active"].get(mac, [])]
            self._today_bytes(out, now)
            self._apps(out, now)
            self._save()
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
        ts = now.timestamp()
        for row in rows:
            mac = row.get("mac")
            if mac not in out:
                continue
            last = st["last"].setdefault(mac, {})
            change = st["app_change"].setdefault(mac, {})
            acc = st["today"].setdefault(mac, {})
            active = st["app_active"].setdefault(mac, {})
            for a in row.get("by_app") or []:
                cat = int(a.get("cat", 0))
                key = str((cat << 16) + int(a.get("app", 0)))
                cur = int(a.get("rx_bytes") or 0) + int(a.get("tx_bytes") or 0)
                delta, slice_ = _step(last, change, key, cur, ts, CATEGORY_ACTIVE_BPS.get(cat, DEFAULT_ACTIVE_BPS))
                if delta:
                    acc[key] = acc.get(key, 0) + delta
                if slice_:
                    active[key] = merge(active.get(key, []) + [slice_])
        for mac, u in out.items():
            for key, b in st["today"].get(mac, {}).items():
                k = int(key)
                name = app_name(k >> 16, k & 0xFFFF)
                u.apps[name] = u.apps.get(name, 0) + b
            for key, iv in st["app_active"].get(mac, {}).items():
                k = int(key)
                name = app_name(k >> 16, k & 0xFFFF)
                u.app_active[name] = merge(u.app_active.get(name, []) + [tuple(x) for x in iv])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.apps_state))
        os.replace(tmp, self.path)


def _step(last: dict, change: dict, key: str, cur: int, ts: float, active_bps: float):
    """Update one running total. Returns (bytes since last poll, in-use time slice or None)."""
    prev = last.get(key)
    last[key] = cur
    if prev is None:  # first time we see it: just remember where it starts
        change[key] = ts
        return 0, None
    delta = cur - prev if cur >= prev else cur  # smaller = counter restarted
    if delta <= 0:
        return 0, None
    span = min(ts - change.get(key, ts), MAX_SLICE)
    change[key] = ts
    if span <= 0 or delta / span < active_bps:
        return delta, None
    return delta, (ts - span, ts)


def human_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"
