"""Base entity: one Home Assistant device per kid, group or device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import DOMAIN, SIGNAL_UPDATE
from .core.state import Restriction


class FamilyWifiEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, target: str | None, key: str) -> None:
        self.hub = entry.runtime_data
        self.target = target
        scope = slugify(target) if target else "all"
        self._attr_unique_id = f"{entry.entry_id}_{scope}_{key}"
        self._attr_translation_key = key
        name = target or "Family WiFi"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{scope}")},
            name=name,
            manufacturer="Family WiFi",
            model="Group" if target in self.hub.cfg.groups else ("Device" if target else "Controller"),
        )

    @property
    def macs(self) -> list[str]:
        return list(self.hub.cfg.resolve(self.target)) if self.target else []

    def restrictions(self, kind: str | None = None) -> list[Restriction]:
        """Active restrictions touching any of this target's devices."""
        macs = set(self.macs)
        return [r for r in self.hub.svc.state.find(kind) if macs & set(r.macs)]

    def covers_all(self, kind: str) -> bool:
        macs = self.macs
        return bool(macs) and all(self.hub.svc.state.find(kind, m) for m in macs)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self.async_write_ha_state))
