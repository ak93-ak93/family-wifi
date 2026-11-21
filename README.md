# Family WiFi

One-tap parental controls for a **UniFi** home network, run from an **iPhone Shortcut** (or Siri).

- ⏸️ **Pause internet** for a kid, a device or a group ("Kids")
- 🐢 **Slow down** their connection (1 Mbps, 256 kbps, …)
- 🚫 **Block websites**: pick a list (Games, Video, Social) or type domains
- ⏲️ **Timers** for all of the above: `15 min`, `2 hours`, `until 7am`, `until I undo`. Timers survive restarts.
- ✅ **Restore** a device or group with one tap. 📋 **Status** shows what's on and when it ends.

```
 iPhone Shortcut / Siri ──HTTPS/HTTP──▶  family-wifi (this app, on a Pi/NAS/Mac at home)
                                                │  local UniFi API
                                                ▼
                                       UniFi console (UDM / UDR / UCG / Cloud Key)
```

UniFi has no simple phone-friendly API for this, so a small service runs at home and
talks to the console's local API. The Shortcut talks to that service.

## How each control works in UniFi

| Control | UniFi mechanism | Notes |
|---|---|---|
| Pause | Client **block** (`cmd/stamgr block-sta`) | Works for Wi-Fi and wired. Fully offline until unblocked. |
| Slow down | **Bandwidth profile** (user group) with a speed cap; device is assigned to it and reconnected | The app creates profiles named `FamilyWiFi …`. Restore puts the device back in its previous profile. |
| Block websites | **Traffic rule** (Network 7.x+), action Block, target Domain, limited to the chosen devices | Domain blocking needs the kids' devices to use the UniFi gateway for DNS. It won't work if a device uses its own DNS, iCloud Private Relay or a VPN. |

## 1. Prepare UniFi

1. In UniFi OS: **Admins & Users → Add Admin**: name `family-wifi`, **Restrict to local access only**,
   role **Site Admin** for Network (or a custom role that can manage clients and traffic rules). Local accounts
   don't need 2FA, which this app can't do.
2. On each kid's iPhone/iPad: **Settings → Wi-Fi → (i) on your network → Private Wi-Fi Address: Off**
   (or set it to *Fixed*). Otherwise the device's MAC address changes and the rules stop applying to it.
3. Write down each device's MAC address from **UniFi Network → Client Devices**.

## 2. Run the service at home

You need something that's always on at home: a Raspberry Pi, NAS, Mac mini, or any Docker host.

```bash
git clone https://github.com/ak93-ak93/family-wifi.git
cd family-wifi
mkdir -p data
cp config.example.yaml data/config.yaml
nano data/config.yaml            # UniFi login, api_token, devices, groups, site lists
docker compose up -d --build
```

Without Docker (Python 3.11+):

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml && nano config.yaml
python -m family_wifi            # listens on port 8787
```

Check it: open `http://<server-ip>:8787` on your phone, paste your `api_token`, and you get a
simple control page. Add it to your Home Screen with **Share → Add to Home Screen** for an app-like icon.

> 💡 Give the server a fixed IP (UniFi: Client Devices → the server → Settings → Fixed IP Address).

## 3. Build the iPhone Shortcut (about 5 minutes, once)

The Shortcut downloads its menus (your kids, devices, speeds, site lists) from the server.
**After you edit `config.yaml` you don't need to change the Shortcut.**

Open **Shortcuts → +** and add these actions in order. Replace `SERVER` with e.g. `http://192.168.1.20:8787`
and `TOKEN` with your `api_token`.

1. **Get Contents of URL** → `SERVER/menu`
   - Show More → Method **GET** → Headers: `Authorization` = `Bearer TOKEN`
2. **Get Dictionary Value** → `actions` from *Contents of URL* → **Choose from List** (prompt "What?")
   → **Set Variable** `Action`
3. **Get Dictionary Value** → `targets` from *Contents of URL* → **Choose from List** (prompt "Who?")
   → **Set Variable** `Target`
4. **Text** (empty) → **Set Variable** `Option`
5. **If** `Action` *contains* `Slow`:
   - **Get Dictionary Value** `speeds` from *Contents of URL* → **Choose from List** → **Set Variable** `Option`
6. **If** `Action` *contains* `websites`:
   - **Get Dictionary Value** `site_lists` from *Contents of URL* → **Choose from List** → **Set Variable** `Option`
7. **Text** `until I undo` → **Set Variable** `Duration`
8. **If** `Action` *does not contain* `Restore`:
   - **Get Dictionary Value** `durations` from *Contents of URL* → **Choose from List** (prompt "How long?")
     → **Set Variable** `Duration`
9. **Get Contents of URL** → `SERVER/do`
   - Method **POST**, Headers: `Authorization` = `Bearer TOKEN`
   - Request Body **JSON**: `action` = `Action`, `target` = `Target`, `option` = `Option`, `duration` = `Duration`
10. **Get Dictionary Value** `message` from *Contents of URL* → **Show Result**

Name it **Family WiFi**. Now "Hey Siri, Family WiFi" works, and you can add it to your Home Screen
or a widget. *Status* ignores the "Who?" choice.

### Optional: one-tap shortcuts

For things you do every day, make a tiny shortcut with just step 9 and fixed values, e.g. **"Kids bedtime"**:

```json
{"action": "pause", "target": "Kids", "duration": "until 7am"}
```

Other examples:

```json
{"action": "slow",    "target": "Alex", "option": "Slow (1 Mbps)", "duration": "1 hour"}
{"action": "sites",   "target": "Sam",  "option": "Games",         "duration": "until 9pm"}
{"action": "sites",   "target": "Kids", "option": "youtube.com, tiktok.com", "duration": "2 hours"}
{"action": "restore", "target": "Kids"}
{"action": "status"}
```

Combine these with **Shortcuts → Automation → Time of Day** for schedules, e.g. a weekday 9 pm bedtime
pause. Turn off "Ask Before Running".

## Using it away from home

The server is only on your home network. To use it on cellular, use the **UniFi Teleport / WireGuard VPN**
(built into UniFi consoles) or Tailscale on the phone. Don't port-forward this service to the internet.

## API

All endpoints except `/` and `/health` need `Authorization: Bearer <api_token>`.

| Method | Path | Body / result |
|---|---|---|
| GET | `/menu` | `{actions, targets, durations, speeds, site_lists}` for building pick lists |
| POST | `/do` | `{action, target, duration?, option?}` → `{ok, message}` |
| GET | `/status` | `{ok, message}` listing active restrictions and end times |

- `action`: `pause` · `slow` · `sites` · `restore` · `status` (menu labels like "⏸️ Pause internet" also work)
- `target`: any group or device name from config (not case sensitive)
- `duration`: `30`, `30 min`, `1.5 hours`, `1 hour 30 min`, `until 7am`, `9:30 pm`, `tomorrow morning`, `until I undo`
- `option`: speed preset for `slow`; site list name or comma-separated domains for `sites`;
  optionally `pause`/`slow`/`sites` for `restore` to undo only that kind

Rules: a new pause or slow-down on a device replaces the earlier one (the new timer wins).
Website blocks stack. Timers are checked every 15 seconds and caught up on startup.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest
```

Tests run against a fake UniFi console (`tests/test_family_wifi.py`), so no hardware is needed.

## Compatibility notes

- Tested against a simulated UniFi OS console only. The API paths match the ones used by the
  `aiounifi` library (used by Home Assistant). The first time you use it on your network, try each action
  on one device and check UniFi to see it applied.
- Bandwidth profiles: if your Network version doesn't support them, *Slow down* returns
  an error from UniFi and the other features keep working.
- Standalone Network app (not a UniFi OS console) is detected automatically. Set `url` to `https://host:8443`.
