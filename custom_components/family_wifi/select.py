"""Selects: '<kid> Speed limit' and '<kid> For how long'."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from . import run_action
from .core.service import DURATIONS
from .entity import FamilyWifiEntity

NO_LIMIT = "No limit"


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    targets = entry.runtime_data.cfg.targets()
    async_add_entities([SpeedSelect(entry, t) for t in targets] + [DurationSelect(entry, t) for t in targets])


class SpeedSelect(FamilyWifiEntity, SelectEntity):
    """Pick a speed to slow down to right away, or 'No limit' to remove the slow-down."""

    _attr_icon = "mdi:speedometer"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "speed_limit")
        self._attr_options = [NO_LIMIT] + [s.name for s in self.hub.cfg.speeds]

    @property
    def current_option(self) -> str:
        if not self.covers_all("slow"):
            return NO_LIMIT
        labels = [r.label for r in self.restrictions("slow")]
        return labels[-1] if labels and labels[-1] in self._attr_options else NO_LIMIT

    async def async_select_option(self, option: str) -> None:
        if option == NO_LIMIT:
            await run_action(self.hass, "restore", self.target, option="slow")
            return
        self.hub.speeds[self.target] = option  # also what the Slow down switch uses next time
        await run_action(self.hass, "slow", self.target, self.hub.duration(self.target), option)


class DurationSelect(FamilyWifiEntity, SelectEntity, RestoreEntity):
    """How long the Internet / Slow down switches and Speed limit apply for. Remembered across restarts."""

    _attr_icon = "mdi:timer-outline"
    _attr_options = DURATIONS

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "duration")

    @property
    def current_option(self) -> str:
        return self.hub.duration(self.target)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in DURATIONS and self.target not in self.hub.durations:
            self.hub.durations[self.target] = last.state

    async def async_select_option(self, option: str) -> None:
        self.hub.durations[self.target] = option
        self.async_write_ha_state()
