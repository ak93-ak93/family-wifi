from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from family_wifi.app import create_app
from family_wifi.config import Config, SpeedPreset, UnifiConfig
from family_wifi.durations import parse_until
from family_wifi.service import FamilyWifi
from family_wifi.state import State
from family_wifi.unifi import UnifiClient, UnifiError

TZ = ZoneInfo("America/Los_Angeles")
from tests.fake_unifi import A, B, C, FakeUnifi


@pytest.fixture
def env(tmp_path):
    cfg = Config(
        unifi=UnifiConfig(url="https://udm", username="u", password="p"),
        api_token="secret",
        timezone="America/Los_Angeles",
        devices={"Alex iPad": [A], "Alex Switch": [B], "Sam iPhone": [C]},
        groups={"Kids": ["Alex iPad", "Alex Switch", "Sam iPhone"], "Alex": ["Alex iPad", "Alex Switch"]},
        site_lists={"Games": ["roblox.com", "fortnite.com"]},
        speeds=[SpeedPreset("Slow (1 Mbps)", 1000, 500)],
        state_file=str(tmp_path / "state.json"),
    )
    fake = FakeUnifi()
    svc = FamilyWifi(cfg, UnifiClient(cfg.unifi, transport=httpx.MockTransport(fake)), State(cfg.state_file))
    return cfg, fake, svc


def test_pause_and_timer_expiry(env):
    cfg, fake, svc = env
    msg = svc.do("pause", "kids", "30 min")
    assert "Paused Kids for 30 min" in msg
    assert fake.blocked == {A, B, C}
    # Fast-forward: make the timer due.
    r = svc.state.find("pause")[0]
    r.until = (svc.now() - timedelta(seconds=1)).isoformat()
    svc.expire()
    assert fake.blocked == set()
    assert svc.state.find() == []


def test_new_pause_replaces_old_timer_and_restore_subset(env):
    cfg, fake, svc = env
    svc.do("pause", "Kids", "until I undo")
    svc.do("pause", "Alex", "1 hour")
    assert len(svc.state.find("pause")) == 2
    assert svc.state.find("pause", A)[0].target == "Alex"
    msg = svc.do("restore", "Sam iPhone")
    assert "Restored" in msg
    assert fake.blocked == {A, B}
    svc.do("restore", "Alex")
    assert fake.blocked == set() and svc.state.find() == []


def test_slow_down_and_restore_previous_group(env):
    cfg, fake, svc = env
    fake.users[A]["usergroup_id"] = "g-default"
    svc.do("🐢 Slow down", "Alex iPad", "2h", option="Slow (1 Mbps)")
    slow_group = next(g for g in fake.groups if g["name"] == "FamilyWiFi Slow (1 Mbps)")
    assert slow_group["qos_rate_max_down"] == 1000
    assert fake.users[A]["usergroup_id"] == slow_group["_id"]
    # Slowing again must still remember the original group.
    svc.do("slow", "Alex iPad", "30m")
    assert any(c[2] == {"cmd": "kick-sta", "mac": A} for c in fake.calls)
    svc.do("restore", "Alex iPad")
    assert fake.users[A]["usergroup_id"] == "g-default"


def test_block_sites_rule_payload_and_partial_restore(env):
    cfg, fake, svc = env
    svc.do("Block websites", "Alex", "until 7am", option="Games")
    (rule,) = fake.rules.values()
    assert rule["action"] == "BLOCK" and rule["matching_target"] == "DOMAIN"
    assert [d["domain"] for d in rule["domains"]] == ["roblox.com", "fortnite.com"]
    assert {t["client_mac"] for t in rule["target_devices"]} == {A, B}
    svc.do("restore", "Alex Switch")
    (rule,) = fake.rules.values()  # recreated for the remaining device only
    assert [t["client_mac"] for t in rule["target_devices"]] == [A]
    svc.do("restore", "Alex iPad")
    assert fake.rules == {}


def test_adhoc_domains(env):
    _, fake, svc = env
    svc.do("sites", "Sam iPhone", "15", option="youtube.com, tiktok.com")
    (rule,) = fake.rules.values()
    assert [d["domain"] for d in rule["domains"]] == ["youtube.com", "tiktok.com"]


def test_state_survives_restart(env):
    cfg, fake, svc = env
    svc.do("pause", "Sam iPhone", "1h")
    again = FamilyWifi(cfg, svc.unifi, State(cfg.state_file))
    assert "Sam iPhone" in again.status()


def test_durations():
    now = datetime(2026, 11, 21, 20, 0, tzinfo=TZ)
    assert parse_until("30 min", now) == now + timedelta(minutes=30)
    assert parse_until("1 hour 30 min", now) == now + timedelta(minutes=90)
    assert parse_until("45", now) == now + timedelta(minutes=45)
    assert parse_until("until 7am", now) == datetime(2026, 11, 22, 7, 0, tzinfo=TZ)
    assert parse_until("9:30 pm", now) == datetime(2026, 11, 21, 21, 30, tzinfo=TZ)
    assert parse_until("until I undo", now) is None
    with pytest.raises(ValueError):
        parse_until("whenever", now)


def test_http_api(env):
    cfg, fake, svc = env
    client = TestClient(create_app(cfg, svc))
    assert client.get("/menu").status_code == 401
    h = {"Authorization": "Bearer secret"}
    menu = client.get("/menu", headers=h).json()
    assert menu["targets"][:2] == ["Kids", "Alex"]
    r = client.post("/do", headers=h, json={"action": "⏸️ Pause internet", "target": "Kids", "duration": "15 min"})
    assert r.status_code == 200 and "Paused" in r.json()["message"]
    assert "Kids" in client.get("/status", headers=h).json()["message"]
    r = client.post("/do", headers=h, json={"action": "pause", "target": "Nobody", "duration": "15 min"})
    assert r.status_code == 400 and "Unknown device" in r.json()["detail"]
    assert "Family WiFi" in client.get("/").text


def test_home_assistant_copy_is_in_sync():
    """custom_components/family_wifi/core must match family_wifi/ (run scripts/sync_core.py)."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ["config.py", "durations.py", "service.py", "state.py", "unifi.py"]:
        assert (root / "family_wifi" / name).read_text() == (
            root / "custom_components/family_wifi/core" / name
        ).read_text(), f"{name} differs: run python scripts/sync_core.py"


def _client(fake, **kw):
    cfg = UnifiConfig(url="https://udm", **kw)
    return UnifiClient(cfg, transport=httpx.MockTransport(fake))


def test_api_key_only():
    fake = FakeUnifi()
    c = _client(fake, api_key="good-key")
    c.block_client(A)
    assert fake.blocked == {A} and c.auth_mode == "api_key"
    assert not any(call[1] == "/api/auth/login" for call in fake.calls)


def test_api_key_rejected_falls_back_to_password():
    fake = FakeUnifi()
    fake.key_allowed = False
    c = _client(fake, api_key="good-key", username="u", password="p")
    c.block_client(A)
    assert fake.blocked == {A} and c.auth_mode == "login"
    c.unblock_client(A)  # stays on login, no second fallback needed
    assert sum(call[1] == "/api/auth/login" for call in fake.calls) == 1


def test_api_key_rejected_without_password_explains():
    fake = FakeUnifi()
    fake.key_allowed = False
    c = _client(fake, api_key="good-key")
    with pytest.raises(UnifiError, match="didn't accept the API key"):
        c.block_client(A)


def test_check_command(tmp_path, capsys, monkeypatch):
    from family_wifi import check, unifi

    fake = FakeUnifi()
    original = unifi.UnifiClient.__init__
    monkeypatch.setattr(unifi.UnifiClient, "__init__",
                        lambda self, cfg, transport=None: original(self, cfg, httpx.MockTransport(fake)))
    monkeypatch.setattr(check.time, "sleep", lambda s: None)
    cfgfile = tmp_path / "config.yaml"
    cfgfile.write_text(f"""
unifi: {{url: "https://udm", api_key: good-key}}
api_token: t0ken
devices: {{Alex iPad: "{A}", Old Phone: "02:11:22:33:44:55"}}
""")
    assert check.main(["--config", str(cfgfile), "--try-pause", "alex ipad", "--seconds", "1"]) == 0
    out = capsys.readouterr().out
    assert "API key" in out and "All good" in out
    assert "never seen: Old Phone" in out
    assert "good-key" not in out
    assert fake.blocked == set()  # restored after the trial pause
