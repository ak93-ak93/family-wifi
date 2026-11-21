"""Minimal client for the local UniFi Network controller API.

Works with UniFi OS consoles (UDM, UDM Pro/SE, UDR, UCG, UX, Cloud Key Gen2+),
which serve the Network app under /proxy/network, and with the standalone
Network application. Paths follow the ones used by the aiounifi library.
"""

from __future__ import annotations

import logging

import httpx

from .config import UnifiConfig

log = logging.getLogger(__name__)


class UnifiError(RuntimeError):
    pass


class UnifiClient:
    def __init__(self, cfg: UnifiConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.http = httpx.Client(
            base_url=cfg.url, verify=cfg.verify_ssl, timeout=15, transport=transport
        )
        self.is_unifi_os: bool | None = None
        self._logged_in = False

    # ---------- session ----------

    def _detect(self) -> None:
        if self.is_unifi_os is not None:
            return
        r = self.http.get("/", follow_redirects=False)
        # UniFi OS answers 200 at "/"; the standalone app redirects to /manage.
        self.is_unifi_os = r.status_code == 200
        log.info("UniFi OS console: %s", self.is_unifi_os)

    def login(self) -> None:
        self._detect()
        path = "/api/auth/login" if self.is_unifi_os else "/api/login"
        r = self.http.post(
            path,
            json={"username": self.cfg.username, "password": self.cfg.password, "remember": True},
        )
        if r.status_code != 200:
            raise UnifiError(f"UniFi login failed ({r.status_code}). Check username/password; use a local admin account without 2FA.")
        if token := r.headers.get("x-csrf-token"):
            self.http.headers["x-csrf-token"] = token
        self._logged_in = True

    def _url(self, path: str, v2: bool = False) -> str:
        site = self.cfg.site
        if v2:
            base = f"/v2/api/site/{site}"
        else:
            base = f"/api/s/{site}"
        return ("/proxy/network" if self.is_unifi_os else "") + base + path

    def request(self, method: str, path: str, json=None, v2: bool = False):
        if not self._logged_in:
            self.login()
        for attempt in (1, 2):
            r = self.http.request(method, self._url(path, v2), json=json)
            if r.status_code == 401 and attempt == 1:
                self.login()
                continue
            break
        if token := r.headers.get("x-csrf-token"):
            self.http.headers["x-csrf-token"] = token
        if r.status_code >= 400:
            raise UnifiError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        if not r.content:
            return None
        body = r.json()
        if not v2:
            meta = body.get("meta", {})
            if meta.get("rc") not in (None, "ok"):
                raise UnifiError(f"{method} {path} -> {meta.get('msg', meta)}")
            return body.get("data", [])
        return body

    # ---------- clients ----------

    def block_client(self, mac: str) -> None:
        self.request("POST", "/cmd/stamgr", {"cmd": "block-sta", "mac": mac})

    def unblock_client(self, mac: str) -> None:
        self.request("POST", "/cmd/stamgr", {"cmd": "unblock-sta", "mac": mac})

    def online_clients(self) -> list[dict]:
        return self.request("GET", "/stat/sta") or []

    def known_client(self, mac: str) -> dict | None:
        data = self.request("GET", f"/stat/user/{mac}") or []
        return data[0] if data else None

    # ---------- bandwidth (user groups / "bandwidth profiles") ----------

    def usergroups(self) -> list[dict]:
        return self.request("GET", "/rest/usergroup") or []

    def ensure_usergroup(self, name: str, down_kbps: int, up_kbps: int) -> str:
        for g in self.usergroups():
            if g.get("name") == name:
                if g.get("qos_rate_max_down") != down_kbps or g.get("qos_rate_max_up") != up_kbps:
                    g.update(qos_rate_max_down=down_kbps, qos_rate_max_up=up_kbps)
                    self.request("PUT", f"/rest/usergroup/{g['_id']}", g)
                return g["_id"]
        created = self.request(
            "POST",
            "/rest/usergroup",
            {"name": name, "qos_rate_max_down": down_kbps, "qos_rate_max_up": up_kbps},
        )
        return created[0]["_id"]

    def default_usergroup_id(self) -> str | None:
        for g in self.usergroups():
            if g.get("attr_no_delete") or g.get("name", "").lower() == "default":
                return g["_id"]
        return None

    def set_client_usergroup(self, mac: str, group_id: str | None) -> str | None:
        """Assign a client to a user group. Returns the group it had before."""
        client = self.known_client(mac)
        if client is None:
            raise UnifiError(f"{mac} has never connected to this network, so it can't be throttled yet")
        previous = client.get("usergroup_id") or None
        target = group_id or self.default_usergroup_id() or ""
        self.request("PUT", f"/rest/user/{client['_id']}", {"usergroup_id": target})
        if mac in {c.get("mac") for c in self.online_clients()}:
            # Reconnect so the new limit applies straight away.
            self.request("POST", "/cmd/stamgr", {"cmd": "kick-sta", "mac": mac})
        return previous

    # ---------- website blocking (Traffic Rules, Network 7.x+) ----------

    def create_domain_block(self, description: str, domains: list[str], macs: list[str]) -> str:
        rule = {
            "action": "BLOCK",
            "app_category_ids": [],
            "app_ids": [],
            "bandwidth_limit": {"download_limit_kbps": 1024, "enabled": False, "upload_limit_kbps": 1024},
            "description": description,
            "domains": [{"domain": d, "ports": [], "port_ranges": []} for d in domains],
            "enabled": True,
            "ip_addresses": [],
            "ip_ranges": [],
            "matching_target": "DOMAIN",
            "network_ids": [],
            "regions": [],
            "schedule": {
                "mode": "ALWAYS",
                "repeat_on_days": [],
                "time_all_day": False,
            },
            "target_devices": [{"client_mac": m, "type": "CLIENT"} for m in macs],
        }
        created = self.request("POST", "/trafficrules", rule, v2=True)
        return created["_id"]

    def delete_traffic_rule(self, rule_id: str) -> None:
        try:
            self.request("DELETE", f"/trafficrules/{rule_id}", v2=True)
        except UnifiError as e:
            if "404" not in str(e):
                raise
