"""The actions a parent can take: pause, slow down, block sites, restore, status."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .durations import describe, parse_until
from .state import Restriction, State
from .unifi import UnifiClient

log = logging.getLogger(__name__)

ACTIONS = {
    "pause": "⏸️ Pause internet",
    "slow": "🐢 Slow down",
    "sites": "🚫 Block websites",
    "restore": "✅ Restore everything",
    "status": "📋 Status",
}
DURATIONS = ["15 min", "30 min", "1 hour", "2 hours", "until 7am", "until I undo"]
SLOW_GROUP_PREFIX = "FamilyWiFi "


def action_key(text: str) -> str:
    """Accept 'pause', '⏸️ Pause internet', 'Pause internet', ..."""
    t = text.strip().casefold()
    for key, label in ACTIONS.items():
        plain = label.split(" ", 1)[1].casefold()
        if t in (key, label.casefold(), plain):
            return key
    aliases = {"block": "pause", "off": "pause", "throttle": "slow", "unblock": "restore",
               "resume": "restore", "undo": "restore", "websites": "sites", "block sites": "sites"}
    if t in aliases:
        return aliases[t]
    raise KeyError(f"Unknown action {text!r}. Use one of: {', '.join(ACTIONS)}")


class FamilyWifi:
    def __init__(self, cfg: Config, unifi: UnifiClient, state: State):
        self.cfg = cfg
        self.unifi = unifi
        self.state = state
        self.tz = ZoneInfo(cfg.timezone)
        self.lock = threading.RLock()

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # ---------- entry point used by the API ----------

    def do(self, action: str, target: str | None = None, duration: str | None = None,
           option: str | None = None, reason: str = "") -> str:
        key = action_key(action)
        if key == "status":
            return self.status()
        if not target:
            raise ValueError("Pick a device or group")
        target = self.cfg.canonical(target)
        with self.lock:
            if key == "restore":
                return self.restore(target, kind=option or None)
            until = parse_until(duration, self.now())
            if key == "pause":
                return self.pause(target, until, reason=reason)
            if key == "slow":
                return self.slow(target, until, option, reason=reason)
            return self.block_sites(target, until, option or "", reason=reason)

    # ---------- actions ----------

    def pause(self, target: str, until: datetime | None, reason: str = "") -> str:
        macs = self.cfg.resolve(target)
        self._take_over("pause", list(macs))
        for mac in macs:
            self.unifi.block_client(mac)
        self._record("pause", target, list(macs), until, reason=reason)
        return f"⏸️ Paused {target} {describe(until, self.now())}."

    def slow(self, target: str, until: datetime | None, speed_name: str | None, reason: str = "") -> str:
        macs = self.cfg.resolve(target)
        speed = self.cfg.speed(speed_name)
        gid = self.unifi.ensure_usergroup(SLOW_GROUP_PREFIX + speed.name, speed.down_kbps, speed.up_kbps)
        # If a device is already slowed, keep the group it had *before* that.
        earlier = {}
        for r in self.state.find("slow"):
            for mac in macs:
                if mac in r.macs:
                    earlier[mac] = r.undo.get("previous", {}).get(mac)
        self._take_over("slow", list(macs), release=False)
        previous = {}
        for mac in macs:
            before = self.unifi.set_client_usergroup(mac, gid)
            previous[mac] = earlier.get(mac, before)
        self._record("slow", target, list(macs), until, label=speed.name, undo={"previous": previous}, reason=reason)
        return f"🐢 {target} slowed to {speed.name} {describe(until, self.now())}."

    def block_sites(self, target: str, until: datetime | None, sites: str, reason: str = "") -> str:
        if not sites:
            raise ValueError("Pick a website list")
        macs = self.cfg.resolve(target)
        label, domains = self.cfg.sites(sites)
        rule_id = self.unifi.create_domain_block(f"FamilyWiFi: {label} for {target}", domains, list(macs))
        self._record("sites", target, list(macs), until, label=label,
                     undo={"rule_id": rule_id, "domains": domains}, reason=reason)
        return f"🚫 Blocked {label} on {target} {describe(until, self.now())}."

    def restore(self, target: str, kind: str | None = None) -> str:
        if kind:
            kind = action_key(kind)
        macs = set(self.cfg.resolve(target))
        undone = []
        for r in list(self.state.find(kind)):
            hit = macs & set(r.macs)
            if hit:
                self._release(r, sorted(hit))
                undone.append(ACTIONS[r.kind].split(" ", 1)[1].lower() + (f" ({r.label})" if r.label else ""))
        if not undone:
            return f"✅ {target} had nothing switched on."
        return f"✅ Restored {target}: removed " + ", ".join(dict.fromkeys(undone)) + "."

    def status(self) -> str:
        now = self.now()
        lines = []
        for r in sorted(self.state.find(), key=lambda r: r.until or "9999"):
            what = ACTIONS[r.kind]
            if r.label:
                what += f" ({r.label})"
            lines.append(f"{what} — {r.target} {describe(r.until_dt(), now)}")
        return "\n".join(lines) if lines else "✅ No restrictions active. Everyone is online."

    # ---------- timers ----------

    def expire(self) -> list[str]:
        """Undo anything whose timer has run out. Called every few seconds."""
        done = []
        now = self.now()
        with self.lock:
            for r in list(self.state.find()):
                until = r.until_dt()
                if until and until <= now:
                    try:
                        self._release(r, list(r.macs))
                        done.append(f"{r.kind} on {r.target} ended")
                        log.info("Timer ended: %s on %s", r.kind, r.target)
                    except Exception:  # keep it; we'll retry next tick
                        log.exception("Could not end %s on %s, will retry", r.kind, r.target)
        return done

    # ---------- helpers ----------

    def _record(self, kind, target, macs, until, label="", undo=None, reason=""):
        self.state.add(Restriction(
            kind=kind, target=target, macs=macs,
            until=until.isoformat() if until else None, label=label,
            undo=undo or {}, created=self.now().isoformat(), reason=reason,
        ))

    def release_reason(self, target: str, prefix: str) -> list[str]:
        """Undo restrictions on `target` that were set for a reason starting with `prefix`."""
        macs = set(self.cfg.resolve(target))
        undone = []
        with self.lock:
            for r in list(self.state.find()):
                hit = macs & set(r.macs)
                if hit and r.reason.startswith(prefix):
                    self._release(r, sorted(hit))
                    undone.append(r.reason)
        return undone

    def _take_over(self, kind: str, macs: list[str], release: bool = False) -> None:
        """A new pause/slow replaces any older one on the same devices (new timer wins)."""
        for r in self.state.find(kind):
            overlap = [m for m in r.macs if m in macs]
            if not overlap:
                continue
            if release:
                self._release(r, overlap)
            else:
                self._drop_macs(r, overlap)

    def _drop_macs(self, r: Restriction, macs: list[str]) -> None:
        r.macs = [m for m in r.macs if m not in macs]
        if "previous" in r.undo:
            r.undo["previous"] = {m: g for m, g in r.undo["previous"].items() if m not in macs}
        if r.macs:
            self.state.save()
        else:
            self.state.remove(r)

    def _release(self, r: Restriction, macs: list[str]) -> None:
        """Undo restriction `r` for `macs` (all of them, or a subset)."""
        if r.kind == "pause":
            for mac in macs:
                self.unifi.unblock_client(mac)
        elif r.kind == "slow":
            for mac in macs:
                self.unifi.set_client_usergroup(mac, r.undo.get("previous", {}).get(mac))
        elif r.kind == "sites":
            self.unifi.delete_traffic_rule(r.undo["rule_id"])
            remaining = [m for m in r.macs if m not in macs]
            if remaining:  # traffic rules can't be shrunk in place; recreate for the rest
                r.undo["rule_id"] = self.unifi.create_domain_block(
                    f"FamilyWiFi: {r.label} for {r.target}", r.undo["domains"], remaining)
        self._drop_macs(r, macs)
