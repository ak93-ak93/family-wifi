"""Family WiFi: pause, slow down and filter kids' internet on a UniFi network."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import httpx
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_SITE,
    CONF_VERIFY_SSL,
    DEFAULT_SITE_LISTS,
    DEFAULT_SPEEDS,
    DOMAIN,
    OPT_GROUPS,
    OPT_NAMES,
    OPT_SITE_LISTS,
    OPT_SPEEDS,
    SIGNAL_UPDATE,
)
from .core.config import Config, SpeedPreset, UnifiConfig, normalize_mac
from .core.service import FamilyWifi
from .core.state import State
from .core.unifi import UnifiClient, UnifiError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
TICK = timedelta(seconds=15)


@dataclass
class Hub:
    cfg: Config
    svc: FamilyWifi


type FamilyWifiConfigEntry = ConfigEntry[Hub]


def unifi_config(data: dict) -> UnifiConfig:
    return UnifiConfig(
        url=data[CONF_URL].rstrip("/"),
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        site=data.get(CONF_SITE, "default"),
        verify_ssl=data.get(CONF_VERIFY_SSL, False),
    )


def build_config(hass: HomeAssistant, entry: ConfigEntry) -> Config:
    """Turn the entry's options into the core Config (group -> device names -> MACs)."""
    opts = entry.options
    names: dict[str, str] = {normalize_mac(m): n for m, n in opts.get(OPT_NAMES, {}).items()}
    groups = {g: [normalize_mac(m) for m in macs] for g, macs in opts.get(OPT_GROUPS, {}).items()}

    devices: dict[str, list[str]] = {}
    by_mac: dict[str, str] = {}
    for macs in groups.values():
        for mac in macs:
            if mac in by_mac:
                continue
            name = names.get(mac) or mac
            if name in devices or name in groups:  # keep names unique
                name = f"{name} ({mac[-5:]})"
            devices[name] = [mac]
            by_mac[mac] = name

    return Config(
        unifi=unifi_config(entry.data),
        api_token="unused-in-home-assistant",
        timezone=str(hass.config.time_zone),
        devices=devices,
        groups={g: [by_mac[m] for m in macs] for g, macs in groups.items()},
        site_lists=opts.get(OPT_SITE_LISTS, DEFAULT_SITE_LISTS),
        speeds=[SpeedPreset(s["name"], int(s["down_kbps"]), int(s["up_kbps"]))
                for s in opts.get(OPT_SPEEDS, DEFAULT_SPEEDS)],
        state_file=hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}.state.json"),
    )


async def async_setup_entry(hass: HomeAssistant, entry: FamilyWifiConfigEntry) -> bool:
    cfg = build_config(hass, entry)

    def _connect() -> FamilyWifi:
        unifi = UnifiClient(cfg.unifi)
        unifi.login()
        return FamilyWifi(cfg, unifi, State(cfg.state_file))

    try:
        svc = await hass.async_add_executor_job(_connect)
    except UnifiError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except httpx.HTTPError as err:
        raise ConfigEntryNotReady(f"Can't reach UniFi at {cfg.unifi.url}: {err}") from err

    entry.runtime_data = Hub(cfg, svc)

    async def _tick(_now=None) -> None:
        try:
            if await hass.async_add_executor_job(svc.expire):
                async_dispatcher_send(hass, SIGNAL_UPDATE)
        except Exception:  # noqa: BLE001 - keep the timer running
            _LOGGER.exception("Checking Family WiFi timers failed")

    await _tick()  # catch up on timers that ended while HA was off
    entry.async_on_unload(async_track_time_interval(hass, _tick, TICK))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FamilyWifiConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await hass.async_add_executor_job(entry.runtime_data.svc.unifi.http.close)
    return ok


# ---------------- actions (services) ----------------

TARGET = vol.Required("target")
DURATION = vol.Optional("duration", default="until I undo")
SERVICES = {
    "pause": ("pause", vol.Schema({TARGET: cv.string, DURATION: cv.string})),
    "slow_down": ("slow", vol.Schema({TARGET: cv.string, DURATION: cv.string, vol.Optional("speed"): cv.string})),
    "block_websites": ("sites", vol.Schema({TARGET: cv.string, DURATION: cv.string, vol.Required("websites"): cv.string})),
    "restore": ("restore", vol.Schema({TARGET: cv.string, vol.Optional("only"): vol.In(["pause", "slow", "sites"])})),
    "status": ("status", vol.Schema({})),
}


async def run_action(hass: HomeAssistant, action: str, target: str | None = None,
                     duration: str | None = None, option: str | None = None) -> str:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Family WiFi isn't set up or can't reach UniFi right now")
    svc: FamilyWifi = entries[0].runtime_data.svc
    try:
        msg = await hass.async_add_executor_job(svc.do, action, target, duration, option)
    except (KeyError, ValueError) as err:
        raise ServiceValidationError(str(err).strip("'\"")) from err
    except (UnifiError, httpx.HTTPError) as err:
        raise HomeAssistantError(f"UniFi said: {err}") from err
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    return msg


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    for name, (action, schema) in SERVICES.items():

        async def handle(call: ServiceCall, action=action) -> ServiceResponse:
            d = call.data
            option = d.get("speed") or d.get("websites") or d.get("only")
            msg = await run_action(hass, action, d.get("target"), d.get("duration"), option)
            return {"message": msg}

        hass.services.async_register(DOMAIN, name, handle, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL)
    return True
