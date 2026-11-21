"""Switches: '<kid> Internet' (off = paused) and '<kid> Slow down'."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from . import run_action
from .entity import FamilyWifiEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    targets = entry.runtime_data.cfg.targets()
    async_add_entities(
        [InternetSwitch(entry, t) for t in targets] + [SlowSwitch(entry, t) for t in targets]
    )


class InternetSwitch(FamilyWifiEntity, SwitchEntity):
    """On = internet allowed. Turning it off pauses for the kid's 'For how long' choice."""

    _attr_icon = "mdi:web"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "internet")

    @property
    def is_on(self) -> bool:
        return not self.covers_all("pause")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_action(self.hass, "pause", self.target, self.hub.duration(self.target))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_action(self.hass, "restore", self.target, option="pause")


class SlowSwitch(FamilyWifiEntity, SwitchEntity):
    """On = speed limited to the kid's 'Speed limit' choice, for their 'For how long' choice."""

    _attr_icon = "mdi:speedometer-slow"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "slow_down")

    @property
    def is_on(self) -> bool:
        return self.covers_all("slow")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_action(self.hass, "slow", self.target, self.hub.duration(self.target), self.hub.speed(self.target))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_action(self.hass, "restore", self.target, option="slow")

