"""Base entity: one Home Assistant device per kid, group or device."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, SIGNAL_UPDATE
from .core.state import Restriction
from .core.usage import Usage


SIBLINGS = {
    "internet": "switch", "slow_down": "switch", "status": "sensor", "online": "binary_sensor",
    "data_today": "sensor", "screen_time": "sensor", "top_app": "sensor", "download_speed": "sensor",
    "speed_limit": "select", "duration": "select",
    "daily_limit": "number", "time_left": "sensor", "extra_time": "button",
}


class FamilyWifiEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, target: str | None, key: str) -> None:
        self.hub = entry.runtime_data
        self.target = target
        scope = slugify(target) if target else "all"
        self._scope_id = f"{entry.entry_id}_{scope}"
        self._attr_unique_id = f"{self._scope_id}_{key}"
        self._attr_translation_key = key
        name = target or "Family WiFi"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{scope}")},
            name=name,
            manufacturer="Family WiFi",
            model="Group" if target in self.hub.cfg.groups else ("Device" if target else "Controller"),
        )

    def sibling_ids(self) -> dict[str, str]:
        """Entity ids of this target's other entities, for the dashboard card."""
        reg = er.async_get(self.hass)
        out = {}
        for key, domain in SIBLINGS.items():
            if eid := reg.async_get_entity_id(domain, DOMAIN, f"{self._scope_id}_{key}"):
                out[key] = eid
        return out

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


class UsageEntity(CoordinatorEntity, FamilyWifiEntity):
    """An entity fed by the usage poller (every 2 minutes)."""

    def __init__(self, entry, target: str, key: str) -> None:
        CoordinatorEntity.__init__(self, entry.runtime_data.usage)
        FamilyWifiEntity.__init__(self, entry, target, key)

    @property
    def usage(self) -> Usage:
        return Usage.combine([self.coordinator.data[m] for m in self.macs if m in (self.coordinator.data or {})])
