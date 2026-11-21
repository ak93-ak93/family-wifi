"""Family WiFi: pause, slow down and filter kids' internet on a UniFi network."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY,
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
from .allowance import Allowances
from .core.usage import Usage, UsageTracker

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)  # set up from the UI only
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]
USAGE_INTERVAL = timedelta(minutes=2)
SAFETY_TICK = timedelta(minutes=5)  # timers are scheduled exactly; this only catches anything missed


@dataclass
class Hub:
    cfg: Config
    svc: FamilyWifi
    usage: DataUpdateCoordinator[dict[str, Usage]]
    tracker: UsageTracker
    # Per kid: how long switches apply for, and which speed "Slow down" uses (set by the select entities).
    durations: dict[str, str] = field(default_factory=dict)
    speeds: dict[str, str] = field(default_factory=dict)
    allowances: Allowances | None = None

    def duration(self, target: str) -> str:
        return self.durations.get(target, "until I undo")

    def speed(self, target: str) -> str:
        return self.speeds.get(target) or self.cfg.speeds[0].name


type FamilyWifiConfigEntry = ConfigEntry[Hub]


def unifi_config(data: dict) -> UnifiConfig:
    return UnifiConfig(
        url=data[CONF_URL].rstrip("/"),
        username=data.get(CONF_USERNAME) or "",
        password=data.get(CONF_PASSWORD) or "",
        site=data.get(CONF_SITE, "default"),
        verify_ssl=data.get(CONF_VERIFY_SSL, False),
        api_key=data.get(CONF_API_KEY) or "",
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

    usage_file = hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}.usage.json")

    def _connect() -> tuple[FamilyWifi, UsageTracker]:
        # Runs in a worker thread: network calls and file reads must never run on HA's event loop.
        unifi = UnifiClient(cfg.unifi)
        unifi.connect()
        unifi.online_clients()  # proves the credentials work
        return FamilyWifi(cfg, unifi, State(cfg.state_file)), UsageTracker(unifi, usage_file)

    try:
        svc, tracker = await hass.async_add_executor_job(_connect)
    except UnifiError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except httpx.HTTPError as err:
        raise ConfigEntryNotReady(f"Can't reach UniFi at {cfg.unifi.url}: {err}") from err

    macs = sorted({m for ms in cfg.devices.values() for m in ms})

    async def _poll_usage() -> dict[str, Usage]:
        try:
            return await hass.async_add_executor_job(tracker.poll, macs, dt_util.now())
        except (UnifiError, httpx.HTTPError) as err:
            raise UpdateFailed(f"Couldn't read usage from UniFi: {err}") from err

    usage = DataUpdateCoordinator(
        hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} usage",
        update_method=_poll_usage, update_interval=USAGE_INTERVAL,
    )
    entry.runtime_data = Hub(cfg, svc, usage, tracker)
    allowances = Allowances(hass, entry)
    await allowances.async_load()
    entry.runtime_data.allowances = allowances

    def _after_usage() -> None:
        if usage.last_update_success:
            entry.async_create_background_task(hass, allowances.async_check(), f"{DOMAIN} allowances")

    entry.async_on_unload(usage.async_add_listener(_after_usage))
    # First usage read in the background: it must never slow down Home Assistant's startup,
    # and usage problems must never block the parental controls.
    entry.async_create_background_task(hass, usage.async_refresh(), f"{DOMAIN} first usage refresh")

    # Timers: ask Home Assistant's scheduler to wake us exactly when the next restriction ends.
    next_timer: list = [None]

    async def _tick(_now=None) -> None:
        try:
            if await hass.async_add_executor_job(svc.expire):
                async_dispatcher_send(hass, SIGNAL_UPDATE)  # also reschedules
        except Exception:  # noqa: BLE001 - keep the timers running
            _LOGGER.exception("Checking Family WiFi timers failed")

    @callback
    def _schedule_next() -> None:
        if next_timer[0]:
            next_timer[0]()
            next_timer[0] = None
        ends = [r.until_dt() for r in svc.state.find() if r.until]
        if ends:
            when = max(min(ends), dt_util.now()) + timedelta(seconds=1)
            next_timer[0] = async_track_point_in_utc_time(hass, _tick, dt_util.as_utc(when))

    def _cancel_next() -> None:
        if next_timer[0]:
            next_timer[0]()

    await _tick()  # catch up on timers that ended while Home Assistant was off
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_UPDATE, _schedule_next))
    entry.async_on_unload(_cancel_next)
    entry.async_on_unload(async_track_time_interval(hass, _tick, SAFETY_TICK))
    _schedule_next()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Now that every entity exists, refresh so the status sensors can list their siblings for the card.
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FamilyWifiConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await hass.async_add_executor_job(entry.runtime_data.svc.unifi.http.close)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Integration deleted: remove what it saved (active timers, usage counters, allowance state)."""
    from pathlib import Path

    from homeassistant.helpers.storage import Store

    def _delete() -> None:
        for name in ("state.json", "usage.json"):
            Path(hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}.{name}")).unlink(missing_ok=True)

    await hass.async_add_executor_job(_delete)
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.allowance").async_remove()


# ---------------- actions (services) ----------------

TARGET = vol.Required("target")
DURATION = vol.Optional("duration", default="until I undo")
SERVICES = {
    "pause": ("pause", vol.Schema({TARGET: cv.string, DURATION: cv.string})),
    "slow_down": ("slow", vol.Schema({TARGET: cv.string, DURATION: cv.string, vol.Optional("speed"): cv.string})),
    "block_websites": ("sites", vol.Schema({TARGET: cv.string, DURATION: cv.string, vol.Required("websites"): cv.string})),
    "restore": ("restore", vol.Schema({TARGET: cv.string, vol.Optional("only"): vol.In(["pause", "slow", "sites"])})),
    "status": ("status", vol.Schema({})),
    "extra_time": ("extra_time", vol.Schema({TARGET: cv.string, vol.Optional("minutes", default=30): vol.All(int, vol.Range(min=1, max=720))})),
}


async def run_action(hass: HomeAssistant, action: str, target: str | None = None,
                     duration: str | None = None, option: str | None = None, reason: str = "") -> str:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Family WiFi isn't set up or can't reach UniFi right now")
    svc: FamilyWifi = entries[0].runtime_data.svc
    try:
        msg = await hass.async_add_executor_job(svc.do, action, target, duration, option, reason)
    except (KeyError, ValueError) as err:
        raise ServiceValidationError(str(err).strip("'\"")) from err
    except (UnifiError, httpx.HTTPError) as err:
        raise HomeAssistantError(f"UniFi said: {err}") from err
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    return msg


CARD_URL = "/family_wifi/family-wifi-card.js"


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the dashboard card and load it on every dashboard (no manual resource needed)."""
    if "frontend" not in hass.config.components or hass.http is None:
        return
    from pathlib import Path

    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.loader import async_get_integration

    version = (await async_get_integration(hass, DOMAIN)).version
    path = Path(__file__).parent / "www" / "family-wifi-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")


async def _extra_time(hass: HomeAssistant, target: str, minutes: int) -> str:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Family WiFi isn't set up or can't reach UniFi right now")
    hub: Hub = entries[0].runtime_data
    try:
        target = hub.cfg.canonical(target)
    except KeyError as err:
        raise ServiceValidationError(str(err).strip("'\"")) from err
    try:
        return await hub.allowances.async_grant(target, minutes)
    except (UnifiError, httpx.HTTPError) as err:
        raise HomeAssistantError(f"UniFi said: {err}") from err


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    try:
        await _register_card(hass)
    except Exception:  # noqa: BLE001 - the card is optional, the integration must still load
        _LOGGER.exception("Couldn't register the Family WiFi dashboard card")
    for name, (action, schema) in SERVICES.items():

        async def handle(call: ServiceCall, action=action) -> ServiceResponse:
            d = call.data
            if action == "extra_time":
                return {"message": await _extra_time(hass, d["target"], d["minutes"])}
            option = d.get("speed") or d.get("websites") or d.get("only")
            msg = await run_action(hass, action, d.get("target"), d.get("duration"), option)
            return {"message": msg}

        hass.services.async_register(DOMAIN, name, handle, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL)
    return True
