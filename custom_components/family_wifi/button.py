"""Button: '<kid> +30 min' adds time to today's limits and lifts any limit that kicked in."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import FamilyWifiEntity

EXTRA_MINUTES = 30


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([ExtraTimeButton(entry, t) for t in entry.runtime_data.allowances.targets()])


class ExtraTimeButton(FamilyWifiEntity, ButtonEntity):

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "extra_time")

    async def async_press(self) -> None:
        await self.hub.allowances.async_grant(self.target, EXTRA_MINUTES)
