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
