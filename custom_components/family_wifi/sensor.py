"""Sensors: '<kid> Status' and an overall 'Active restrictions' count."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity

from .core.service import ACTIONS
from .entity import FamilyWifiEntity

KIND_WORD = {"pause": "Paused", "slow": "Slowed", "sites": "Websites blocked"}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    targets = entry.runtime_data.cfg.targets()
    async_add_entities([StatusSensor(entry, t) for t in targets] + [ActiveSensor(entry)])


def _describe(r) -> dict:
    return {
        "what": ACTIONS[r.kind].split(" ", 1)[1],
        "who": r.target,
        "detail": r.label or None,
        "ends_at": r.until,  # ISO time, or None = until undone
    }


class StatusSensor(FamilyWifiEntity, SensorEntity):
    """'Normal', 'Paused', 'Slowed', 'Websites blocked' (or a combination)."""

    _attr_icon = "mdi:shield-account"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "status")

    @property
    def native_value(self) -> str:
        kinds = dict.fromkeys(r.kind for r in self.restrictions())
        return ", ".join(KIND_WORD[k] for k in kinds) if kinds else "Normal"

    @property
    def extra_state_attributes(self) -> dict:
        rs = self.restrictions()
        ends = [r.until for r in rs if r.until]
        return {"restrictions": [_describe(r) for r in rs], "next_change": min(ends) if ends else None}


class ActiveSensor(FamilyWifiEntity, SensorEntity):
    """Number of active restrictions across the house, with a readable summary."""

    _attr_icon = "mdi:router-network"
    _attr_native_unit_of_measurement = "restrictions"

    def __init__(self, entry) -> None:
        super().__init__(entry, None, "active")

    @property
    def native_value(self) -> int:
        return len(self.hub.svc.state.find())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "summary": self.hub.svc.status(),
            "restrictions": [_describe(r) for r in self.hub.svc.state.find()],
        }
