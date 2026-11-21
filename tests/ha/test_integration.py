from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.family_wifi.const import DEFAULT_SITE_LISTS, DEFAULT_SPEEDS, DOMAIN
from tests.fake_unifi import A, B, C

pytestmark = pytest.mark.asyncio

DATA = {"url": "https://udm", "username": "u", "password": "p", "site": "default", "verify_ssl": False}
OPTIONS = {
    "groups": {"Kids": [A, B, C], "Alex": [A, B]},
    "names": {A: "Alex iPad", B: "Alex Switch", C: "Sam iPhone"},
    "site_lists": DEFAULT_SITE_LISTS,
    "speeds": DEFAULT_SPEEDS,
}


async def setup(hass, options=OPTIONS):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=options, title="Family WiFi")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass, fake_unifi):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["groups"] == {}
    await hass.async_block_till_done()


async def test_config_flow_bad_login(hass, fake_unifi):
    fake_unifi.reject_login = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_add_group(hass, fake_unifi):
    entry = await setup(hass, {**OPTIONS, "groups": {}, "names": {}})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "group"})
    assert result["step_id"] == "group_edit"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"name": "Sam", "devices": [C]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["groups"] == {"Sam": [C]}
    assert entry.options["names"] == {C: "Sam iPhone"}
    # Entry reloaded with the new kid's entities.
    assert hass.states.get("switch.sam_internet").state == "on"


async def test_options_websites_and_speeds(hass, fake_unifi):
    entry = await setup(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "websites"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"list": "__new__"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"name": "Homework", "domains": "x"})
    assert flow["errors"] == {"domains": "bad_domains"}
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"name": "Homework", "domains": "netflix.com\nhulu.com"})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["site_lists"]["Homework"] == ["netflix.com", "hulu.com"]

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "speeds"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"speeds": "Snail = 128/64\nOK = 3000"})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["speeds"] == [
        {"name": "Snail", "down_kbps": 128, "up_kbps": 64},
        {"name": "OK", "down_kbps": 3000, "up_kbps": 3000},
    ]


async def test_internet_switch_pauses_and_restores(hass, fake_unifi):
    await setup(hass)
    assert hass.states.get("switch.kids_internet").state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.kids_internet"}, blocking=True)
    assert fake_unifi.blocked == {A, B, C}
    assert hass.states.get("switch.kids_internet").state == "off"
    assert hass.states.get("switch.alex_ipad_internet").state == "off"
    assert hass.states.get("sensor.kids_status").state == "Paused"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.alex_internet"}, blocking=True)
    assert fake_unifi.blocked == {C}
    assert hass.states.get("switch.kids_internet").state == "on"  # not every kid is paused any more


async def test_slow_switch(hass, fake_unifi):
    await setup(hass)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.sam_iphone_slow_down"}, blocking=True)
    assert fake_unifi.users[C]["usergroup_id"].startswith("g")
    assert hass.states.get("sensor.sam_iphone_status").state == "Slowed"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.sam_iphone_slow_down"}, blocking=True)
    assert fake_unifi.users[C]["usergroup_id"] == "g-default"


async def test_actions_with_timer(hass, fake_unifi):
    await setup(hass)
    resp = await hass.services.async_call(
        DOMAIN, "pause", {"target": "alex", "duration": "30 min"}, blocking=True, return_response=True)
    assert "Paused Alex for 30 min" in resp["message"]
    await hass.services.async_call(
        DOMAIN, "block_websites", {"target": "Sam iPhone", "websites": "Games", "duration": "2 hours"}, blocking=True)
    assert len(fake_unifi.rules) == 1
    assert hass.states.get("sensor.family_wifi_active_restrictions").state == "2"
    status = await hass.services.async_call(DOMAIN, "status", {}, blocking=True, return_response=True)
    assert "Alex" in status["message"] and "Games" in status["message"]

    # Pretend 31 minutes have passed: the pause is now due, the 2-hour block isn't.
    hub = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    for r in hub.svc.state.find("pause"):
        r.until = (dt_util.now() - timedelta(seconds=1)).isoformat()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done(wait_background_tasks=True)  # timers run as background tasks
    assert fake_unifi.blocked == set()
    assert hass.states.get("switch.alex_internet").state == "on"
    assert len(fake_unifi.rules) == 1

    await hass.services.async_call(DOMAIN, "restore", {"target": "Kids"}, blocking=True)
    assert fake_unifi.rules == {}
    assert hass.states.get("sensor.family_wifi_active_restrictions").state == "0"


async def test_bad_target(hass, fake_unifi):
    await setup(hass)
    with pytest.raises(ServiceValidationError, match="Unknown device"):
        await hass.services.async_call(DOMAIN, "pause", {"target": "Nobody"}, blocking=True)


async def test_unload(hass, fake_unifi):
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow_api_key_only(hass, fake_unifi):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "https://udm", "api_key": "good-key", "site": "default", "verify_ssl": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "username" not in result["data"]
    await hass.async_block_till_done()
    assert not any(c[1] == "/api/auth/login" for c in fake_unifi.calls)


async def test_config_flow_needs_some_auth(hass, fake_unifi):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "https://udm", "site": "default", "verify_ssl": False})
    assert result["errors"] == {"base": "need_auth"}


async def test_bad_api_key_starts_reauth(hass, fake_unifi):
    entry = MockConfigEntry(domain=DOMAIN, data={"url": "https://udm", "api_key": "wrong"}, options=OPTIONS)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_usage_entities(hass, fake_unifi, freezer):
    now = dt_util.now()
    hour_ms = int(now.replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
    fake_unifi.reports["5minutes"][A] = [{"time": hour_ms, "rx_bytes": 5_000_000, "tx_bytes": 300_000_000}]
    fake_unifi.dpi[A] = [{"cat": 4, "app": 112, "rx_bytes": 0, "tx_bytes": 100_000_000}]
    entry = await setup(hass)

    assert hass.states.get("binary_sensor.alex_online").state == "on"
    assert hass.states.get("binary_sensor.alex_online").attributes["devices_online"] == ["Alex iPad"]
    assert hass.states.get("binary_sensor.sam_iphone_online").state == "off"
    data = hass.states.get("sensor.alex_data_used_today")
    assert float(data.state) == pytest.approx(305) and data.attributes["unit_of_measurement"] == "MB"
    assert float(hass.states.get("sensor.kids_download_speed").state) == pytest.approx(2.0)  # 250 kB/s
    assert hass.states.get("sensor.alex_top_app_today").state == "None yet"  # first poll = starting point

    # Two minutes later, 200 MB more YouTube.
    fake_unifi.dpi[A] = [{"cat": 4, "app": 112, "rx_bytes": 0, "tx_bytes": 300_000_000}]
    freezer.tick(timedelta(minutes=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    top = hass.states.get("sensor.kids_top_app_today")
    assert top.state == "Youtube"
    assert top.attributes["apps"] == {"Youtube": "200.0 MB"}
    assert top.attributes["traffic_identification"] == "on"
    # 200 MB in ~3 min is far above the video threshold: counted as watching.
    screen = hass.states.get("sensor.alex_screen_time_today")
    assert screen.attributes["unit_of_measurement"] == "min"
    assert screen.attributes["app_minutes"] == {"Youtube": 3}
    assert screen.attributes["app_time"] == {"Youtube": "3 min"}
    assert top.attributes["app_time"] == {"Youtube": "3 min"}


async def test_usage_failure_does_not_break_controls(hass, fake_unifi):
    fake_unifi.dpi_enabled = False
    await setup(hass)
    assert hass.states.get("sensor.alex_top_app_today").state == "unavailable"
    assert hass.states.get("binary_sensor.alex_online").state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.alex_internet"}, blocking=True)
    assert fake_unifi.blocked == {A, B}


async def test_card_attributes(hass, fake_unifi):
    await setup(hass)
    alex = hass.states.get("sensor.alex_status").attributes
    assert alex["family_wifi_target"] == "Alex" and alex["is_group"] is True
    assert alex["devices"] == ["Alex iPad", "Alex Switch"]
    assert alex["entities"]["internet"] == "switch.alex_internet"
    assert alex["entities"]["screen_time"] == "sensor.alex_screen_time_today"
    assert alex["entities"]["online"] == "binary_sensor.alex_online"
    assert hass.states.get("sensor.alex_ipad_status").attributes["is_group"] is False
    menus = hass.states.get("sensor.family_wifi_active_restrictions").attributes
    assert menus["site_lists"] == ["Games", "Video", "Social"]
    assert menus["speeds"][0] == "Slow (1 Mbps)" and "until 7am" in menus["durations"]


async def test_card_is_served_and_loaded(hass, fake_unifi, hass_client):
    from unittest.mock import patch

    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    add_js.assert_called_once()
    assert add_js.call_args.args[1].startswith("/family_wifi/family-wifi-card.js?v=")
    client = await hass_client()
    resp = await client.get("/family_wifi/family-wifi-card.js")
    assert resp.status == 200
    assert 'customElements.define("family-wifi-card"' in await resp.text()


async def test_speed_limit_select(hass, fake_unifi):
    await setup(hass)
    sel = hass.states.get("select.sam_iphone_speed_limit")
    assert sel.state == "No limit"
    assert sel.attributes["options"] == ["No limit", "Slow (1 Mbps)", "Very slow (256 kbps)", "Homework (5 Mbps)"]

    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.sam_iphone_speed_limit", "option": "Very slow (256 kbps)"}, blocking=True)
    group = next(g for g in fake_unifi.groups if g["name"] == "FamilyWiFi Very slow (256 kbps)")
    assert group["qos_rate_max_down"] == 256 and fake_unifi.users[C]["usergroup_id"] == group["_id"]
    assert hass.states.get("select.sam_iphone_speed_limit").state == "Very slow (256 kbps)"
    assert hass.states.get("switch.sam_iphone_slow_down").state == "on"

    # Switch to another speed: the device keeps its original group to go back to.
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.sam_iphone_speed_limit", "option": "Homework (5 Mbps)"}, blocking=True)
    assert hass.states.get("select.sam_iphone_speed_limit").state == "Homework (5 Mbps)"
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.sam_iphone_speed_limit", "option": "No limit"}, blocking=True)
    assert fake_unifi.users[C]["usergroup_id"] == "g-default"
    assert hass.states.get("switch.sam_iphone_slow_down").state == "off"

    # The Slow down switch now uses the last speed picked.
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.sam_iphone_slow_down"}, blocking=True)
    assert hass.states.get("select.sam_iphone_speed_limit").state == "Homework (5 Mbps)"


async def test_duration_select_applies_to_switches(hass, fake_unifi):
    entry = await setup(hass)
    assert hass.states.get("select.alex_for_how_long").state == "until I undo"
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.alex_for_how_long", "option": "30 min"}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.alex_internet"}, blocking=True)
    (r,) = entry.runtime_data.svc.state.find("pause")
    left = (dt_util.parse_datetime(r.until) - dt_util.now()).total_seconds()
    assert 29 * 60 < left <= 30 * 60
    ends = hass.states.get("sensor.alex_status").attributes["restrictions"][0]["ends_at"]
    assert ends == r.until


async def test_duration_select_remembered_after_restart(hass, fake_unifi):
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State("select.alex_for_how_long", "1 hour")])
    await setup(hass)
    assert hass.states.get("select.alex_for_how_long").state == "1 hour"
