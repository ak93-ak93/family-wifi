# Family WiFi

Parental controls for a **UniFi** home network, in **Home Assistant**: pause the internet, slow it down
or block websites per kid, with timers, daily screen-time limits and usage stats.

<img src="docs/card-light.png" width="360" alt="Family WiFi dashboard card: Alex online with games blocked, Sam paused until 7am">

## What it does

- ⏸️ **Pause** a kid, a device or a group ("Kids") for 30 min, 1 hour, until 7am, or until you undo it.
- 🐢 **Slow down** their connection: 5 Mbps for homework, 1 Mbps, 256 kbps, or your own speeds.
- 🚫 **Block websites**: lists like Games, Video and Social, or any domains you type.
- ⏱️ **Daily limits**: "3 hours of screen time" or "1 hour of YouTube", then pause or block until 7am.
  **+30 min** with one tap.
- 📊 **See usage**: who's online, data used today, top apps, estimated screen time and time per app.
- 📱 **One dashboard card** for all of it, and the switches work in **Apple Home and Siri**.

## How it works

```
Home Assistant (Family WiFi integration) ──local API──▶ UniFi console (UDM / UDR / UCG / Cloud Key)
```

The integration talks straight to your UniFi console on your home network. No cloud service, and no
extra server. It uses three UniFi features:

| Control | UniFi feature |
|---|---|
| Pause | Blocks the device (Wi-Fi and wired) |
| Slow down | Moves the device into a bandwidth profile with a speed cap |
| Block websites | A domain traffic rule for just those devices |

## Quick start

You need Home Assistant 2025.8 or newer, HACS, and a UniFi console running UniFi Network.

**1. Give the integration access to UniFi.** Use an API key, a local account, or (recommended) both:

- **API key** (UniFi Network 9+): UniFi Network → **Settings → Control Plane → Integrations → Create API Key**.
- **Local account**: UniFi OS → **Admins & Users → Add Admin**, tick **Restrict to local access only**,
  role **Site Admin** for Network, no two-factor authentication.

  On some UniFi versions, API keys only work for part of what this integration needs. With both set, the key is
  tried first and the account takes over if UniFi refuses it.

**2. Fix the kids' device addresses.** On each iPhone/iPad: **Settings → Wi-Fi → ⓘ next to your network →
Private Wi-Fi Address → Off** (or *Fixed*). Otherwise the device's address keeps changing and the limits stop
following it. Android has a similar "MAC address type" setting.

**3. Install.** HACS → ⋮ → **Custom repositories** → `https://github.com/ak93-ak93/family-wifi`, type
**Integration** → **Family WiFi** → **Download** → restart Home Assistant.

**4. Connect.** **Settings → Devices & services → Add integration → Family WiFi**. Enter the console address
(e.g. `https://192.168.1.1`) and your API key and/or account. Leave *Verify SSL* off; UniFi uses its own
certificate.

**5. Add your kids.** **Family WiFi → Configure → Add or edit a kid or group**: name it ("Alex", or "Kids" for
everyone) and pick their devices from the list UniFi knows.

**6. Add the card.** Edit a dashboard → **Add card** → **Family WiFi**. Nothing else to install.

**Test it:** pause one device from the card. Its internet should stop within seconds. Then press **Restore**.

**Updates** appear in **Settings → Updates** with release notes. Install, then restart Home Assistant.

## Everyday use

**The card** does everything with taps: Internet on/off, quick pauses, **Slow…** and **Block…** (choose a speed
or website list and how long), **Restore**, and **+30 min**. It shows who's online, current limits with end
times, screen time, data and top apps. It follows your theme, light or dark.

**Each kid also gets regular Home Assistant entities**, for your own dashboards and automations:

- **Internet** switch: off pauses, on restores.
- **Slow down** switch, and a **Speed limit** dropdown to pick the speed.
- **For how long** dropdown: how long the switches apply (15 min … until 7am, or until you undo it).
- **Status**, **Online**, **Data used today**, **Screen time today**, **Top app today** and **Download speed**.

**Actions** for scripts and automations: `family_wifi.pause`, `slow_down`, `block_websites`, `restore`,
`extra_time` and `status`. For example:

```yaml
action: family_wifi.pause
data:
  target: Kids
  duration: until 7am
```

**Bedtime and school hours:** use Home Assistant's own **Schedule** helper and an automation. The
[reference](docs/reference.md#bedtime-with-the-schedule-helper) has a ready-to-paste example.

**Apple Home and Siri:** share the **Internet** and **Slow down** switches with the built-in **HomeKit Bridge**
integration, or with a Matter bridge add-on. Then *"Hey Siri, turn off Alex Internet"* pauses Alex for their
*For how long* choice. Home Assistant's **Matter Server** add-on can't do this: it only lets Home Assistant
control Matter devices, not share its own. [Details](docs/reference.md#apple-home-and-siri).

## Daily limits

**Family WiFi → Configure → Daily limits**, per kid:

- **Daily screen time** in minutes (0 = no limit). You can also change it from any dashboard.
- **When it runs out**: pause the internet, or slow it down, until 7am.
- **App limits**, one per line, with an optional website list to block:

  ```
  Youtube = 60, Video
  Roblox Game = 45, Games
  Instagram = 30
  ```

  After 60 minutes of YouTube, the Video list is blocked until 7am. Without a list, the internet is paused.

**+30 min** (card or button) adds time for today and lifts a limit that already kicked in. Pauses you set
yourself stay. You get a warning event at 10 minutes left and a notification when time is up; the
[reference](docs/reference.md#notify-your-phone) shows how to send them to your phone. Limits reset at midnight.

## Usage and screen time

Refreshed every 2 minutes:

- **Online** and **speed right now**: from UniFi's live client list.
- **Data used today**: from UniFi's own usage reports, the same numbers as the UniFi app, with daily history.
- **Top apps**: from UniFi's **Traffic Identification**. Turn it on in UniFi (Settings → Traffic Management or
  Security, depending on version). App names come from [unpoller](https://github.com/unpoller/unifi). Apps that
  list doesn't know, like TikTok, show as e.g. *Other Media Streaming*.
- **Screen time and time per app are estimates.** UniFi can't see the screen, so time counts as "in use" while
  traffic stays above a level that means real use. Video needs about 0.2 Mbps, so background syncing doesn't
  count. Time on two devices at once counts once.
  - Undercounts downloaded videos, reading and offline games.
  - Overcounts a video left playing with nobody watching.
  - Treat it as a good guide, not a stopwatch, and leave some slack in limits.

## What it can't do

- **Show websites visited.** UniFi doesn't record which sites a device opens; top apps are the closest it gets.
- **Block websites for devices that skip your network's DNS.** Website blocking relies on the device using the
  UniFi gateway for DNS. A VPN, iCloud Private Relay or a custom DNS setting gets around it. Pausing still works.
- **Follow a device whose address keeps changing.** That's why Private Wi-Fi Address has to be off (step 2).
- **Slow down on every UniFi version.** It uses UniFi bandwidth profiles. If your version doesn't have them,
  *Slow down* shows an error and everything else keeps working.

## Troubleshooting

- **Logs:** Settings → System → Logs, search for `family_wifi`. Errors say what UniFi refused.
- **Diagnostics:** Settings → Devices & services → Family WiFi → ⋮ → **Download diagnostics**. Passwords, keys
  and device addresses are removed, so it's safe to share.
- **"UniFi rejected the API key or the username/password"**: check the key or account from step 1. Accounts with
  two-factor authentication can't be used.
- **A device doesn't show up when adding a kid**: it has to have connected to your network at least once.
- **Top app says "unavailable"**: turn on Traffic Identification in UniFi.
- **Data used today says "unavailable"**: your UniFi version doesn't provide usage reports.

## More

- [Reference](docs/reference.md): every entity, action, event, card option and automation example.
- [Development](#development).

## Development

```bash
pip install -r requirements_test.txt
python -m pytest                  # all tests, against a fake UniFi console
node tests/card/run.js            # dashboard card in a browser with a mock Home Assistant (needs Playwright)
```

| Path | What's there |
|---|---|
| `custom_components/family_wifi/` | The integration: setup screens, entities, actions, daily limits, diagnostics |
| `custom_components/family_wifi/core/` | UniFi logic with no Home Assistant imports: console API, restrictions and timers, usage and screen time |
| `custom_components/family_wifi/www/` | The dashboard card |
| `tests/` | `test_core.py` (UniFi logic), `ha/` (the integration in a real Home Assistant test instance), `card/` |

- **No blocking calls:** the Home Assistant tests run under *blockbuster*, the same tool Home Assistant core
  uses, so nothing may block Home Assistant's event loop. All UniFi calls run in worker threads.
- **Releasing:** bump `version` in `custom_components/family_wifi/manifest.json` and merge to `main`. A GitHub
  release is published automatically, and HACS offers it as an update.
- **App names:** `python scripts/update_dpi_names.py` regenerates them from unpoller.

Tested against a simulated UniFi console and Home Assistant 2026.2. The UniFi API calls follow the
[aiounifi](https://github.com/Kane610/aiounifi) library that Home Assistant's UniFi integration uses.

## License

[MIT](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
