"""Who and what Family WiFi controls: UniFi connection, kids/groups/devices, website lists, speeds."""

from __future__ import annotations

from dataclasses import dataclass, field


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
        """Every kid/group, then every device: groups first."""
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
