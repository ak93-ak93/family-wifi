"""Sensors: '<kid> Status' and an overall 'Active restrictions' count."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfDataRate, UnitOfInformation, UnitOfTime
from homeassistant.util import dt as dt_util

from .core.service import ACTIONS
from .core.usage import human_bytes, human_duration
from .entity import FamilyWifiEntity, UsageEntity

KIND_WORD = {"pause": "Paused", "slow": "Slowed", "sites": "Websites blocked"}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    targets = entry.runtime_data.cfg.targets()
    entities = [StatusSensor(entry, t) for t in targets] + [ActiveSensor(entry)]
    for t in targets:
        entities += [DataTodaySensor(entry, t), SpeedSensor(entry, t, "download"),
                     SpeedSensor(entry, t, "upload"), TopAppSensor(entry, t), ScreenTimeSensor(entry, t)]
    async_add_entities(entities)


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


class DataTodaySensor(UsageEntity, SensorEntity):
    """Data used since midnight (download + upload), from UniFi's usage reports."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:chart-donut"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "data_today")

    @property
    def native_value(self) -> int | None:
        return self.usage.today_bytes

    @property
    def available(self) -> bool:
        return super().available and self.hub.tracker.reports_available is not False


class SpeedSensor(UsageEntity, SensorEntity):
    """Current download or upload speed."""

    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfDataRate.BYTES_PER_SECOND
    _attr_suggested_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_suggested_display_precision = 1

    def __init__(self, entry, target: str, direction: str) -> None:
        super().__init__(entry, target, f"{direction}_speed")
        self.direction = direction
        self._attr_icon = "mdi:download" if direction == "download" else "mdi:upload"

    @property
    def native_value(self) -> float:
        u = self.usage
        return u.down_bps if self.direction == "download" else u.up_bps


class TopAppSensor(UsageEntity, SensorEntity):
    """Most-used app today; the attributes list the top 10 with amounts."""

    _attr_icon = "mdi:apps"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "top_app")

    @property
    def native_value(self) -> str:
        top = self.usage.top_apps(1)
        return top[0][0] if top else "None yet"

    @property
    def extra_state_attributes(self) -> dict:
        top = self.usage.top_apps(10)
        u = self.usage
        return {
            "apps": {name: human_bytes(b) for name, b in top},
            "apps_mb": {name: round(b / 1e6, 1) for name, b in top},
            "app_time": {name: human_duration(t) for name, t in u.app_times()},
            "traffic_identification": {True: "on", False: "off in UniFi", None: "unknown"}[self.hub.tracker.dpi_available],
        }

    @property
    def available(self) -> bool:
        return super().available and self.hub.tracker.dpi_available is not False


def _clock(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")


class ScreenTimeSensor(UsageEntity, SensorEntity):
    """Estimated time in use today, worked out from data flow (not measured on the device).

    Attributes give minutes per app, e.g. app_minutes: {"Youtube": 65}, for automations.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:timer-sand"

    def __init__(self, entry, target: str) -> None:
        super().__init__(entry, target, "screen_time")

    @property
    def native_value(self) -> int:
        return round(self.usage.active_seconds / 60)

    @property
    def extra_state_attributes(self) -> dict:
        u = self.usage
        times = u.app_times(10)
        return {
            "screen_time": human_duration(u.active_seconds),
            "app_time": {name: human_duration(t) for name, t in times},
            "app_minutes": {name: round(t / 60) for name, t in times},
            "first_active": _clock(u.active[0][0]) if u.active else None,
            "last_active": _clock(u.active[-1][1]) if u.active else None,
            "estimate": True,
        }
