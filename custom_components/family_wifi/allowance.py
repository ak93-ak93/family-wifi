"""Daily allowances: screen time and per-app limits, checked after every usage poll.

Config (entry.options["allowances"]), per kid:
    {"screen_minutes": 180, "action": "pause" | "slow",
     "apps": {"Youtube": {"minutes": 60, "block": "Video"}}}   # block "" = pause instead

When a limit is used up, the kid is paused / slowed / gets a website list blocked until 7am.
Those restrictions carry a reason starting with "Limit:", so "+30 min" lifts only them.
Events for your own automations: family_wifi_limit_warning (10 min left) and family_wifi_limit_reached.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OPT_ALLOWANCES, SIGNAL_UPDATE

_LOGGER = logging.getLogger(__name__)
LIMIT_PREFIX = "Limit:"
WARN_MINUTES = 10
UNTIL = "until 7am"


class Allowances:
    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.allowance")
        # {"date": "YYYY-MM-DD", "bonus": {kid: minutes}, "fired": {kid: ["screen", "warn", "app:Youtube"]}}
        self.data: dict = {"date": "", "bonus": {}, "fired": {}}
        self._lock = asyncio.Lock()  # one check or grant at a time, so a limit never fires twice

    async def async_load(self) -> None:
        self.data = await self.store.async_load() or self.data
        self._rollover()

    # ----- reading -----

    @property
    def hub(self):
        return self.entry.runtime_data

    def targets(self) -> list[str]:
        """Kids that can have allowances: groups if there are any, else devices."""
        cfg = self.hub.cfg
        return list(cfg.groups) or list(cfg.devices)

    def config(self, target: str) -> dict:
        return self.entry.options.get(OPT_ALLOWANCES, {}).get(target, {})

    def bonus(self, target: str) -> int:
        self._rollover()
        return int(self.data["bonus"].get(target, 0))

    def used(self, target: str) -> tuple[float, dict[str, float]]:
        """(screen minutes, {app: minutes}) used today."""
        u = self.hub.tracker.for_macs(self.hub.cfg.resolve(target))
        return u.active_seconds / 60, {a: t / 60 for a, t in u.app_times(50)}

    def screen_limit(self, target: str) -> int | None:
        minutes = int(self.config(target).get("screen_minutes") or 0)
        return minutes + self.bonus(target) if minutes else None

    def left(self, target: str) -> float | None:
        limit = self.screen_limit(target)
        if limit is None:
            return None
        return max(0.0, limit - self.used(target)[0])

    def apps_left(self, target: str) -> dict[str, float]:
        _, app_used = self.used(target)
        used_cf = {a.casefold(): m for a, m in app_used.items()}
        return {
            app: max(0.0, int(rule.get("minutes", 0)) + self.bonus(target) - used_cf.get(app.casefold(), 0))
            for app, rule in self.config(target).get("apps", {}).items()
        }

    # ----- checking -----

    async def async_check(self) -> None:
        async with self._lock:
            await self._check_all()

    async def _check_all(self) -> None:
        self._rollover()
        changed = False
        for target in self.targets():
            try:
                changed |= await self._check_target(target)
            except HomeAssistantError as err:
                _LOGGER.warning("Couldn't apply %s's daily limit: %s", target, err)
        if changed:
            await self.store.async_save(self.data)
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def _check_target(self, target: str) -> bool:
        from . import run_action

        cfg = self.config(target)
        fired = self.data["fired"].setdefault(target, [])
        screen_used, _ = self.used(target)
        changed = False

        limit = self.screen_limit(target)
        if limit is not None:
            left = limit - screen_used
            if left <= 0 and "screen" not in fired:
                action = cfg.get("action", "pause")
                await run_action(self.hass, action, target, UNTIL,
                                 self.hub.speed(target) if action == "slow" else None,
                                 reason=f"{LIMIT_PREFIX} daily screen time")
                fired.append("screen")
                self._announce(target, "screen_time", None, limit,
                               f"{target} used their {_fmt(limit)} of screen time today, so the internet is "
                               f"{'paused' if action == 'pause' else 'slowed down'} until 7am.")
                changed = True
            elif 0 < left <= WARN_MINUTES and "warn" not in fired:
                fired.append("warn")
                self.hass.bus.async_fire(f"{DOMAIN}_limit_warning",
                                         {"target": target, "limit": "screen_time", "minutes_left": round(left)})
                changed = True

        for app, left in self.apps_left(target).items():
            key = f"app:{app}"
            if left <= 0 and key not in fired:
                rule = cfg["apps"][app]
                block = rule.get("block") or ""
                if block:
                    await run_action(self.hass, "sites", target, UNTIL, block, reason=f"{LIMIT_PREFIX} {app}")
                    what = f"{block} websites are blocked"
                else:
                    await run_action(self.hass, "pause", target, UNTIL, reason=f"{LIMIT_PREFIX} {app}")
                    what = "the internet is paused"
                fired.append(key)
                self._announce(target, "app", app, int(rule.get("minutes", 0)) + self.bonus(target),
                               f"{target} used their {_fmt(int(rule.get('minutes', 0)) + self.bonus(target))} "
                               f"of {app} today, so {what} until 7am.")
                changed = True
        return changed

    def _announce(self, target: str, limit: str, app: str | None, minutes: int, message: str) -> None:
        self.hass.bus.async_fire(f"{DOMAIN}_limit_reached",
                                 {"target": target, "limit": limit, "app": app, "minutes": minutes, "message": message})
        persistent_notification.async_create(
            self.hass, message, title="Family WiFi", notification_id=f"{DOMAIN}_limit_{target}_{app or 'screen'}")

    # ----- extra time -----

    async def async_grant(self, target: str, minutes: int) -> str:
        """Add minutes to today's limits for `target` and lift limits that already kicked in."""
        async with self._lock:
            return await self._grant(target, minutes)

    async def _grant(self, target: str, minutes: int) -> str:
        self._rollover()
        self.data["bonus"][target] = self.bonus(target) + minutes
        self.data["fired"][target] = []
        undone = await self.hass.async_add_executor_job(self.hub.svc.release_reason, target, LIMIT_PREFIX)
        await self.store.async_save(self.data)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        msg = f"⏱️ {target} got {minutes} more minutes today"
        return msg + (" and is back online." if undone else ".")

    def _rollover(self) -> None:
        today = dt_util.now().date().isoformat()
        if self.data.get("date") != today:
            self.data = {"date": today, "bonus": {}, "fired": {}}


def _fmt(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"
