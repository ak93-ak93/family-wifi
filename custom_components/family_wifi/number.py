"""Number: '<kid> Daily screen time' allowance in minutes (0 = no limit)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime

from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import OPT_ALLOWANCES, SIGNAL_UPDATE
from .entity import FamilyWifiEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([DailyLimitNumber(entry, t) for t in entry.runtime_data.allowances.targets()])


class DailyLimitNumber(FamilyWifiEntity, NumberEntity):
    _attr_icon = "mdi:timer-cog-outline"
    _attr_native_min_value = 0
    _attr_native_max_value = 720
    _attr_native_step = 15
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "daily_limit")
        self._entry = entry

    @property
    def native_value(self) -> int:
        return int(self.hub.allowances.config(self.target).get("screen_minutes") or 0)

    async def async_set_native_value(self, value: float) -> None:
        allowances = {k: dict(v) for k, v in self._entry.options.get(OPT_ALLOWANCES, {}).items()}
        allowances.setdefault(self.target, {})["screen_minutes"] = int(value)
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, OPT_ALLOWANCES: allowances})
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)  # refresh "time left" and the card
        await self.hub.allowances.async_check()
