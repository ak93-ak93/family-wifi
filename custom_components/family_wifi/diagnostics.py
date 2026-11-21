"""Settings → Devices & services → Family WiFi → ⋮ → Download diagnostics (secrets and MACs removed)."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME

from .const import CONF_API_KEY, OPT_GROUPS, OPT_NAMES

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_API_KEY, CONF_URL, OPT_NAMES, "macs", "unique_id"}


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    hub = entry.runtime_data
    options = dict(entry.options)
    options[OPT_GROUPS] = {g: f"{len(m)} device(s)" for g, m in options.get(OPT_GROUPS, {}).items()}
    restrictions = [
        {"kind": r.kind, "target": r.target, "devices": len(r.macs), "until": r.until, "label": r.label, "reason": r.reason}
        for r in hub.svc.state.find()
    ]
    return {
        "entry": async_redact_data({"data": dict(entry.data), "options": options}, TO_REDACT),
        "unifi": {"unifi_os": hub.svc.unifi.is_unifi_os, "auth_mode": hub.svc.unifi.auth_mode},
        "usage": {
            "last_update_success": hub.usage.last_update_success,
            "reports_available": hub.tracker.reports_available,
            "traffic_identification": hub.tracker.dpi_available,
        },
        "targets": hub.cfg.targets(),
        "restrictions": restrictions,
        "allowances": {"date": hub.allowances.data.get("date"), "extra_minutes": hub.allowances.data.get("bonus")},
    }
