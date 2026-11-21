"""Setup screens: connect to UniFi, then manage kids/groups, website lists and speeds."""

from __future__ import annotations

import re
from typing import Any

import httpx
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_API_KEY,
    CONF_SITE,
    CONF_VERIFY_SSL,
    DEFAULT_SITE_LISTS,
    DEFAULT_SPEEDS,
    DOMAIN,
    OPT_ALLOWANCES,
    OPT_GROUPS,
    OPT_NAMES,
    OPT_SITE_LISTS,
    OPT_SPEEDS,
)
from .core.config import normalize_mac
from .core.unifi import UnifiClient, UnifiError

NEW = "__new__"
PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _login(data: dict) -> list[dict]:
    """Log in and return all known clients (runs in the executor)."""
    from . import unifi_config

    client = UnifiClient(unifi_config(data))
    try:
        client.connect()
        return client.all_clients()
    finally:
        client.http.close()


async def _check(hass, data: dict) -> dict[str, str]:
    if not data.get(CONF_API_KEY) and not (data.get(CONF_USERNAME) and data.get(CONF_PASSWORD)):
        return {"base": "need_auth"}
    try:
        await hass.async_add_executor_job(_login, data)
    except UnifiError:
        return {"base": "invalid_auth"}
    except httpx.HTTPError:
        return {"base": "cannot_connect"}
    return {}


class FamilyWifiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {k: v for k, v in user_input.items() if v not in ("", None)}
            errors = await _check(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="Family WiFi",
                    data=user_input,
                    options={OPT_GROUPS: {}, OPT_NAMES: {}, OPT_SITE_LISTS: DEFAULT_SITE_LISTS, OPT_SPEEDS: DEFAULT_SPEEDS},
                )
        schema = vol.Schema({
            vol.Required(CONF_URL, default="https://192.168.1.1"): str,
            vol.Optional(CONF_API_KEY): PASSWORD,
            vol.Optional(CONF_USERNAME): str,
            vol.Optional(CONF_PASSWORD): PASSWORD,
            vol.Required(CONF_SITE, default="default"): str,
            vol.Required(CONF_VERIFY_SSL, default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **{k: v for k, v in user_input.items() if v not in ("", None)}}
            errors = await _check(self.hass, data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        schema = vol.Schema({
            vol.Optional(CONF_API_KEY): PASSWORD,
            vol.Optional(CONF_USERNAME, description={"suggested_value": entry.data.get(CONF_USERNAME)}): str,
            vol.Optional(CONF_PASSWORD): PASSWORD,
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> FamilyWifiOptionsFlow:
        return FamilyWifiOptionsFlow()


class FamilyWifiOptionsFlow(OptionsFlowWithReload):
    """Menu: add/edit a kid or group, remove one, website lists, speeds."""

    def __init__(self) -> None:
        self._editing: str | None = None

    @property
    def _opts(self) -> dict[str, Any]:
        o = self.config_entry.options
        return {
            OPT_GROUPS: dict(o.get(OPT_GROUPS, {})),
            OPT_NAMES: dict(o.get(OPT_NAMES, {})),
            OPT_SITE_LISTS: dict(o.get(OPT_SITE_LISTS, DEFAULT_SITE_LISTS)),
            OPT_SPEEDS: list(o.get(OPT_SPEEDS, DEFAULT_SPEEDS)),
            OPT_ALLOWANCES: {k: dict(v) for k, v in o.get(OPT_ALLOWANCES, {}).items()},
        }

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        menu = ["group", "websites", "speeds"]
        if self._opts[OPT_GROUPS]:
            menu.insert(1, "remove_group")
            menu.insert(2, "limits")
        return self.async_show_menu(step_id="init", menu_options=menu)

    # ----- kids / groups -----

    async def async_step_group(self, user_input=None) -> ConfigFlowResult:
        groups = self._opts[OPT_GROUPS]
        if not groups:
            return await self.async_step_group_edit()
        if user_input is not None:
            self._editing = None if user_input["group"] == NEW else user_input["group"]
            return await self.async_step_group_edit()
        options = [SelectOptionDict(value=NEW, label="➕ New kid or group")] + [
            SelectOptionDict(value=g, label=g) for g in groups
        ]
        schema = vol.Schema({vol.Required("group", default=NEW): SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="group", data_schema=schema)

    async def async_step_group_edit(self, user_input=None) -> ConfigFlowResult:
        opts = self._opts
        errors: dict[str, str] = {}
        try:
            clients = await self.hass.async_add_executor_job(_login, self.config_entry.data)
        except (UnifiError, httpx.HTTPError):
            return self.async_abort(reason="cannot_connect")
        known = {normalize_mac(c["mac"]): c.get("name") or c.get("hostname") or c["mac"] for c in clients if c.get("mac")}
        known.update({m: n for m, n in opts[OPT_NAMES].items() if m not in known})

        if user_input is not None:
            name = user_input["name"].strip()
            macs = [normalize_mac(m) for m in user_input["devices"]]
            if not name:
                errors["name"] = "required"
            elif not macs:
                errors["devices"] = "pick_devices"
            elif name != self._editing and name in opts[OPT_GROUPS]:
                errors["name"] = "name_taken"
            else:
                if self._editing and self._editing != name:
                    opts[OPT_GROUPS].pop(self._editing, None)
                opts[OPT_GROUPS][name] = macs
                for m in macs:
                    opts[OPT_NAMES][m] = known.get(m, m)
                return self.async_create_entry(data=opts)

        current = opts[OPT_GROUPS].get(self._editing or "", [])
        choices = [SelectOptionDict(value=m, label=f"{n}  ({m})")
                   for m, n in sorted(known.items(), key=lambda kv: kv[1].lower())]
        schema = vol.Schema({
            vol.Required("name", default=self._editing or ""): str,
            vol.Required("devices", default=current): SelectSelector(
                SelectSelectorConfig(options=choices, multiple=True, mode=SelectSelectorMode.DROPDOWN)),
        })
        return self.async_show_form(step_id="group_edit", data_schema=schema, errors=errors)

    async def async_step_remove_group(self, user_input=None) -> ConfigFlowResult:
        opts = self._opts
        if user_input is not None:
            for g in user_input["groups"]:
                opts[OPT_GROUPS].pop(g, None)
            in_use = {m for macs in opts[OPT_GROUPS].values() for m in macs}
            opts[OPT_NAMES] = {m: n for m, n in opts[OPT_NAMES].items() if m in in_use}
            return self.async_create_entry(data=opts)
        schema = vol.Schema({vol.Required("groups"): SelectSelector(
            SelectSelectorConfig(options=list(opts[OPT_GROUPS]), multiple=True, mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="remove_group", data_schema=schema)

    # ----- website lists -----

    async def async_step_websites(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._editing = None if user_input["list"] == NEW else user_input["list"]
            return await self.async_step_websites_edit()
        options = [SelectOptionDict(value=NEW, label="➕ New website list")] + [
            SelectOptionDict(value=k, label=k) for k in self._opts[OPT_SITE_LISTS]
        ]
        schema = vol.Schema({vol.Required("list", default=NEW): SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="websites", data_schema=schema)

    async def async_step_websites_edit(self, user_input=None) -> ConfigFlowResult:
        opts = self._opts
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["name"].strip()
            domains = [d.lower() for d in re.split(r"[\s,]+", user_input.get("domains", "")) if d]
            lists = opts[OPT_SITE_LISTS]
            if self._editing:
                lists.pop(self._editing, None)
            if user_input.get("delete"):
                return self.async_create_entry(data=opts)
            if not name:
                errors["name"] = "required"
            elif name in lists:
                errors["name"] = "name_taken"
            elif not domains or not all("." in d for d in domains):
                errors["domains"] = "bad_domains"
            else:
                lists[name] = domains
                return self.async_create_entry(data=opts)
        current = opts[OPT_SITE_LISTS].get(self._editing or "", [])
        fields: dict = {
            vol.Required("name", default=self._editing or ""): str,
            vol.Required("domains", default="\n".join(current)): TextSelector(TextSelectorConfig(multiline=True)),
        }
        if self._editing:
            fields[vol.Optional("delete", default=False)] = BooleanSelector()
        return self.async_show_form(step_id="websites_edit", data_schema=vol.Schema(fields), errors=errors)

    # ----- daily limits -----

    async def async_step_limits(self, user_input=None) -> ConfigFlowResult:
        kids = list(self._opts[OPT_GROUPS])
        if user_input is not None:
            self._editing = user_input["kid"]
            return await self.async_step_limits_edit()
        schema = vol.Schema({vol.Required("kid", default=kids[0]): SelectSelector(
            SelectSelectorConfig(options=kids, mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="limits", data_schema=schema)

    async def async_step_limits_edit(self, user_input=None) -> ConfigFlowResult:
        opts = self._opts
        current = opts[OPT_ALLOWANCES].get(self._editing, {})
        lists = opts[OPT_SITE_LISTS]
        errors: dict[str, str] = {}
        if user_input is not None:
            apps: dict = {}
            for line in user_input.get("apps", "").splitlines():
                if not line.strip():
                    continue
                m = re.fullmatch(r"\s*(.+?)\s*=\s*(\d+)\s*(?:,\s*(.+?))?\s*", line)
                block = ""
                if m and m.group(3):
                    block = next((k for k in lists if k.casefold() == m.group(3).casefold()), "")
                if not m or (m.group(3) and not block):
                    errors["apps"] = "bad_apps"
                    break
                apps[m.group(1)] = {"minutes": int(m.group(2)), "block": block}
            if not errors:
                opts[OPT_ALLOWANCES][self._editing] = {
                    "screen_minutes": int(user_input["screen_minutes"]),
                    "action": user_input["action"],
                    "apps": apps,
                }
                return self.async_create_entry(data=opts)
        apps_text = "\n".join(
            f"{app} = {r['minutes']}" + (f", {r['block']}" if r.get("block") else "")
            for app, r in current.get("apps", {}).items())
        schema = vol.Schema({
            vol.Required("screen_minutes", default=int(current.get("screen_minutes", 0))): NumberSelector(
                NumberSelectorConfig(min=0, max=720, step=15, unit_of_measurement="min", mode=NumberSelectorMode.BOX)),
            vol.Required("action", default=current.get("action", "pause")): SelectSelector(SelectSelectorConfig(
                options=["pause", "slow"], translation_key="limit_action", mode=SelectSelectorMode.LIST)),
            vol.Optional("apps", default=apps_text): TextSelector(TextSelectorConfig(multiline=True)),
        })
        return self.async_show_form(step_id="limits_edit", data_schema=schema, errors=errors,
                                    description_placeholders={"kid": self._editing, "lists": ", ".join(lists)})

    # ----- speeds -----

    async def async_step_speeds(self, user_input=None) -> ConfigFlowResult:
        opts = self._opts
        errors: dict[str, str] = {}
        if user_input is not None:
            speeds = []
            for line in user_input["speeds"].splitlines():
                if not line.strip():
                    continue
                m = re.fullmatch(r"\s*(.+?)\s*=\s*(\d+)\s*(?:/\s*(\d+))?\s*", line)
                if not m:
                    errors["speeds"] = "bad_speeds"
                    break
                speeds.append({"name": m.group(1), "down_kbps": int(m.group(2)), "up_kbps": int(m.group(3) or m.group(2))})
            if not errors and not speeds:
                errors["speeds"] = "bad_speeds"
            if not errors:
                opts[OPT_SPEEDS] = speeds
                return self.async_create_entry(data=opts)
        text = "\n".join(f"{s['name']} = {s['down_kbps']}/{s['up_kbps']}" for s in opts[OPT_SPEEDS])
        schema = vol.Schema({vol.Required("speeds", default=text): TextSelector(TextSelectorConfig(multiline=True))})
        return self.async_show_form(step_id="speeds", data_schema=schema, errors=errors)
