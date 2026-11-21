"""Load and validate config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class UnifiConfig:
    url: str  # e.g. https://192.168.1.1
    username: str = ""
    password: str = ""
    site: str = "default"
    verify_ssl: bool = False
    # UniFi OS API key (Network 9+). Used first; username/password is the fallback.
    api_key: str = ""

    @property
    def has_login(self) -> bool:
        return bool(self.username and self.password)


@dataclass
class SpeedPreset:
    name: str
    down_kbps: int
    up_kbps: int


@dataclass
class Config:
    unifi: UnifiConfig
    api_token: str
    timezone: str = "America/Los_Angeles"
    # person/device name -> list of MAC addresses
    devices: dict[str, list[str]] = field(default_factory=dict)
    # group name -> list of device names (e.g. "Kids": ["Alex", "Sam"])
    groups: dict[str, list[str]] = field(default_factory=dict)
    # site list name -> domains (e.g. "Games": ["roblox.com", "fortnite.com"])
    site_lists: dict[str, list[str]] = field(default_factory=dict)
    speeds: list[SpeedPreset] = field(default_factory=list)
    state_file: str = "state.json"

    def targets(self) -> list[str]:
        """Names a Shortcut can pick from: groups first, then devices."""
        return list(self.groups) + [d for d in self.devices if d not in self.groups]

    def canonical(self, target: str) -> str:
        key = _match(target, list(self.groups) + list(self.devices))
        if key is None:
            raise KeyError(f"Unknown device or group: {target!r}")
        return key

    def resolve(self, target: str) -> dict[str, str]:
        """Turn a group or device name into {mac: device_name}."""
        key = _match(target, list(self.groups) + list(self.devices))
        if key is None:
            raise KeyError(f"Unknown device or group: {target!r}")
        names = self.groups.get(key, [key])
        macs: dict[str, str] = {}
        for name in names:
            dkey = _match(name, list(self.devices))
            if dkey is None:
                raise KeyError(f"Group {key!r} lists unknown device {name!r}")
            for mac in self.devices[dkey]:
                macs[normalize_mac(mac)] = dkey
        return macs

    def speed(self, name: str | None) -> SpeedPreset:
        if not self.speeds:
            raise KeyError("No speed presets configured")
        if not name:
            return self.speeds[0]
        key = _match(name, [s.name for s in self.speeds])
        if key is None:
            raise KeyError(f"Unknown speed preset: {name!r}")
        return next(s for s in self.speeds if s.name == key)

    def sites(self, name: str) -> tuple[str, list[str]]:
        """Return (label, domains). Accepts a list name or comma separated domains."""
        key = _match(name, list(self.site_lists))
        if key is not None:
            return key, self.site_lists[key]
        domains = [d.strip().lower() for d in name.split(",") if d.strip()]
        if not domains or not all("." in d for d in domains):
            raise KeyError(f"Unknown site list: {name!r}")
        return ", ".join(domains), domains


def _match(name: str, options: list[str]) -> str | None:
    wanted = name.strip().casefold()
    for opt in options:
        if opt.casefold() == wanted:
            return opt
    return None


def normalize_mac(mac: str) -> str:
    mac = mac.strip().lower().replace("-", ":")
    if len(mac) == 12 and ":" not in mac:
        mac = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
    return mac


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("FAMILY_WIFI_CONFIG", "config.yaml"))
    raw = yaml.safe_load(path.read_text()) or {}
    u = raw.get("unifi") or {}
    unifi = UnifiConfig(
        url=os.environ.get("UNIFI_URL", u.get("url", "")).rstrip("/"),
        username=os.environ.get("UNIFI_USERNAME", u.get("username", "")),
        password=os.environ.get("UNIFI_PASSWORD", u.get("password", "")),
        site=u.get("site", "default"),
        verify_ssl=bool(u.get("verify_ssl", False)),
        api_key=os.environ.get("UNIFI_API_KEY", u.get("api_key", "") or ""),
    )
    token = os.environ.get("FAMILY_WIFI_TOKEN", raw.get("api_token", ""))
    if not unifi.url:
        raise ValueError("unifi.url is required")
    if not unifi.api_key and not unifi.has_login:
        raise ValueError("Set unifi.api_key, or unifi.username and unifi.password (or both)")
    if not token or token == "change-me":
        raise ValueError("Set api_token in config.yaml (or FAMILY_WIFI_TOKEN) to a long random string")
    speeds = [
        SpeedPreset(name=s["name"], down_kbps=int(s["down_kbps"]), up_kbps=int(s.get("up_kbps", s["down_kbps"])))
        for s in raw.get("speeds") or []
    ] or [SpeedPreset("Slow (1 Mbps)", 1000, 500)]
    devices = {name: ([m] if isinstance(m, str) else list(m)) for name, m in (raw.get("devices") or {}).items()}
    return Config(
        unifi=unifi,
        api_token=token,
        timezone=raw.get("timezone", "America/Los_Angeles"),
        devices=devices,
        groups={k: list(v) for k, v in (raw.get("groups") or {}).items()},
        site_lists={k: [d.lower() for d in v] for k, v in (raw.get("site_lists") or {}).items()},
        speeds=speeds,
        state_file=raw.get("state_file", os.environ.get("FAMILY_WIFI_STATE", "state.json")),
    )
