"""Binary sensor: '<kid> Online' (any of their devices connected right now)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import UsageEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([OnlineSensor(entry, t) for t in entry.runtime_data.cfg.targets()])


class OnlineSensor(UsageEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _unrecorded_attributes = frozenset({"devices_online"})

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "online")

    @property
    def is_on(self) -> bool:
        return self.usage.online

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        names = {m: n for n, ms in self.hub.cfg.devices.items() for m in ms}
        return {"devices_online": [names.get(m, m) for m in self.macs if data.get(m) and data[m].online]}
