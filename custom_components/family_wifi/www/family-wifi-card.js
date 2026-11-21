/*
 * Family WiFi card for Home Assistant.
 * Loaded automatically by the Family WiFi integration. Add it to a dashboard with:
 *
 *   type: custom:family-wifi-card
 *   # optional:
 *   title: Family WiFi
 *   targets: [Alex, Sam]        # default: every kid/group set up in Family WiFi
 *   show_devices: false         # true = also show individual devices
 *   pauses: ["30 min", "1 hour", "until 7am"]
 */

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const clock = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return t;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  return d.toDateString() === tomorrow.toDateString() ? `${t} tomorrow` : `${t} ${d.toLocaleDateString([], { weekday: "short" })}`;
};

const WHAT = { "Pause internet": "Paused", "Slow down": "Slowed", "Block websites": "Blocked" };

class FamilyWifiCard extends HTMLElement {
  constructor() {
    super();
    this._ids = new Set();
  }

  setConfig(config) {
    this._config = { pauses: ["30 min", "1 hour", "until 7am"], show_devices: false, ...config };
    this._panel = null; // {target, mode: "slow"|"sites", option, duration}
    this._render();
  }

  set hass(hass) {
    const old = this._hass;
    this._hass = hass;
    if (!old || this._relevantChanged(old, hass)) this._render();
  }

  getCardSize() {
    return 2 + 4 * this._kids().length;
  }

  static getStubConfig() {
    return {};
  }

  // ---------- data ----------

  _relevantChanged(old, hass) {
    for (const id in hass.states) {
      if (old.states[id] !== hass.states[id] && (id.includes("family_wifi") || hass.states[id].attributes.family_wifi_target !== undefined || this._ids.has(id))) {
        return true;
      }
    }
    return false;
  }

  _menus() {
    const s = Object.values(this._hass?.states || {}).find(
      (st) => st.entity_id.startsWith("sensor.") && Array.isArray(st.attributes.site_lists) && Array.isArray(st.attributes.durations)
    );
    return s ? s.attributes : { site_lists: [], speeds: [], durations: ["15 min", "30 min", "1 hour", "2 hours", "until 7am", "until I undo"] };
  }

  _kids() {
    if (!this._hass) return [];
    const all = Object.values(this._hass.states).filter(
      (st) => st.entity_id.startsWith("sensor.") && st.attributes.family_wifi_target !== undefined && st.attributes.family_wifi_target !== null
    );
    let kids = all;
    if (this._config.targets?.length) {
      const order = this._config.targets.map((t) => String(t).toLowerCase());
      kids = all
        .filter((s) => order.includes(String(s.attributes.family_wifi_target).toLowerCase()))
        .sort((a, b) => order.indexOf(a.attributes.family_wifi_target.toLowerCase()) - order.indexOf(b.attributes.family_wifi_target.toLowerCase()));
    } else if (!this._config.show_devices && all.some((s) => s.attributes.is_group)) {
      kids = all.filter((s) => s.attributes.is_group);
    }
    this._ids = new Set(kids.flatMap((k) => Object.values(k.attributes.entities || {})));
    return kids;
  }

  _st(id) {
    return id ? this._hass.states[id] : undefined;
  }

  _fmt(id) {
    const st = this._st(id);
    if (!st || st.state === "unavailable" || st.state === "unknown") return "–";
    return this._hass.formatEntityState ? this._hass.formatEntityState(st) : `${st.state} ${st.attributes.unit_of_measurement || ""}`;
  }

  // ---------- actions ----------

  _call(service, data, message) {
    this._hass
      .callService("family_wifi", service, data)
      .then(() => this._toast(message))
      .catch((e) => this._toast(`⚠️ ${e.message || e}`));
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _onClick(ev) {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const { act, target, value } = b.dataset;
    const kid = this._kids().find((k) => k.attributes.family_wifi_target === target);
    const ent = kid?.attributes.entities || {};
    if (act === "internet") {
      const on = this._st(ent.internet)?.state === "on";
      this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: ent.internet });
      this._toast(on ? `⏸️ Paused ${target}` : `✅ ${target} is back online`);
    } else if (act === "pause") {
      this._call("pause", { target, duration: value }, `⏸️ Paused ${target} (${value})`);
    } else if (act === "restore") {
      this._call("restore", { target }, `✅ Restored ${target}`);
    } else if (act === "open") {
      const m = this._menus();
      const options = value === "slow" ? m.speeds : m.site_lists;
      this._panel = this._panel?.target === target && this._panel.mode === value ? null : { target, mode: value, option: options[0], duration: "1 hour" };
      this._render();
    } else if (act === "option" || act === "duration") {
      this._panel[act] = value;
      this._render();
    } else if (act === "apply") {
      const p = this._panel;
      this._panel = null;
      if (p.mode === "slow") this._call("slow_down", { target: p.target, speed: p.option, duration: p.duration }, `🐢 Slowed ${p.target} (${p.duration})`);
      else this._call("block_websites", { target: p.target, websites: p.option, duration: p.duration }, `🚫 Blocked ${p.option} for ${p.target} (${p.duration})`);
      this._render();
    } else if (act === "cancel") {
      this._panel = null;
      this._render();
    }
  }

  // ---------- view ----------

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    }
    if (!this._hass) return;
    const kids = this._kids();
    const title = this._config.title ?? "Family WiFi";
    const body = kids.length
      ? kids.map((k) => this._kid(k)).join("")
      : `<div class="empty">No kids set up yet. Go to Settings → Devices &amp; services → Family WiFi → Configure.</div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${title ? `<div class="title">${esc(title)}</div>` : ""}${body}</ha-card>`;
  }

  _kid(k) {
    const a = k.attributes;
    const name = a.family_wifi_target;
    const e = a.entities || {};
    const online = this._st(e.online)?.state === "on";
    const internetOn = this._st(e.internet)?.state !== "off";
    const restrictions = (a.restrictions || [])
      .map((r) => `<span class="pill ${r.what === "Pause internet" ? "bad" : "warn"}">${esc(WHAT[r.what] || r.what)}${r.detail ? ` · ${esc(r.detail)}` : ""}${r.ends_at ? ` · until ${esc(clock(r.ends_at))}` : ""}</span>`)
      .join("");
    const top = this._st(e.top_app)?.attributes || {};
    const times = top.app_time || {};
    const apps = Object.entries(top.apps || {})
      .slice(0, 4)
      .map(([n, amount]) => `<span class="app">${esc(n)} <b>${esc(times[n] || amount)}</b></span>`)
      .join("");
    const p = this._panel?.target === name ? this._panel : null;
    const t = esc(name);
    return `
      <div class="kid">
        <div class="head">
          <div class="avatar ${online ? "on" : ""}">${esc(name.slice(0, 1).toUpperCase())}</div>
          <div class="who">
            <div class="name">${t}</div>
            <div class="sub">${online ? "Online" : "Offline"}${restrictions ? "" : " · No limits"}</div>
          </div>
          <button class="toggle ${internetOn ? "on" : "off"}" data-act="internet" data-target="${t}" aria-pressed="${internetOn}" title="Internet on/off">
            <span class="knob"></span><span class="lbl">${internetOn ? "Internet on" : "Paused"}</span>
          </button>
        </div>
        ${restrictions ? `<div class="pills">${restrictions}</div>` : ""}
        <div class="stats">
          <div><span>Screen time</span><b>${esc(this._fmt(e.screen_time))}</b></div>
          <div><span>Data today</span><b>${esc(this._fmt(e.data_today))}</b></div>
          <div><span>Speed now</span><b>${esc(this._fmt(e.download_speed))}</b></div>
        </div>
        ${apps ? `<div class="apps">${apps}</div>` : ""}
        <div class="actions">
          ${this._config.pauses.map((d) => `<button data-act="pause" data-target="${t}" data-value="${esc(d)}">⏸️ ${esc(d)}</button>`).join("")}
          <button data-act="open" data-target="${t}" data-value="slow" class="${p?.mode === "slow" ? "sel" : ""}">🐢 Slow…</button>
          <button data-act="open" data-target="${t}" data-value="sites" class="${p?.mode === "sites" ? "sel" : ""}">🚫 Block…</button>
          <button data-act="restore" data-target="${t}" class="restore">↺ Restore</button>
        </div>
        ${p ? this._panelView(p) : ""}
      </div>`;
  }

  _panelView(p) {
    const m = this._menus();
    const options = p.mode === "slow" ? m.speeds : m.site_lists;
    const chip = (act, v, sel) => `<button class="chip ${v === sel ? "sel" : ""}" data-act="${act}" data-target="${esc(p.target)}" data-value="${esc(v)}">${esc(v)}</button>`;
    return `
      <div class="panel">
        <div class="lab">${p.mode === "slow" ? "Speed" : "Websites"}</div>
        <div class="chips">${options.map((o) => chip("option", o, p.option)).join("")}</div>
        <div class="lab">For how long</div>
        <div class="chips">${m.durations.map((d) => chip("duration", d, p.duration)).join("")}</div>
        <div class="row">
          <button class="go" data-act="apply" data-target="${esc(p.target)}">${p.mode === "slow" ? "Slow down" : "Block"} ${esc(p.target)}</button>
          <button data-act="cancel" data-target="${esc(p.target)}">Cancel</button>
        </div>
      </div>`;
  }
}

const CSS = `
  ha-card { padding: 4px 0 8px; }
  .title { font-size: 1.25rem; font-weight: 500; padding: 12px 16px 4px; color: var(--primary-text-color); }
  .empty { padding: 16px; color: var(--secondary-text-color); }
  .kid { padding: 12px 16px; border-top: 1px solid var(--divider-color); }
  .kid:first-of-type { border-top: none; }
  .head { display: flex; align-items: center; gap: 12px; }
  .avatar { position: relative; flex: none; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center;
    font-weight: 600; color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .avatar::after { content: ""; position: absolute; right: -1px; bottom: -1px; width: 12px; height: 12px; border-radius: 50%;
    background: var(--disabled-text-color, #999); border: 2px solid var(--card-background-color, #fff); }
  .avatar.on::after { background: var(--success-color, #43a047); }
  .who { flex: 1; min-width: 0; }
  .name { font-size: 1.05rem; font-weight: 500; color: var(--primary-text-color); }
  .sub { font-size: .85rem; color: var(--secondary-text-color); }
  button { font: inherit; font-size: .85rem; cursor: pointer; border-radius: 18px; padding: 6px 12px;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button:hover { border-color: var(--primary-color); }
  button.sel { border-color: var(--primary-color); color: var(--primary-color); }
  .toggle { display: flex; align-items: center; gap: 8px; padding: 4px 12px 4px 4px; border-radius: 20px; }
  .toggle .knob { width: 34px; height: 20px; border-radius: 10px; position: relative; background: var(--disabled-text-color, #999); transition: background .2s; }
  .toggle .knob::after { content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: left .2s; }
  .toggle.on .knob { background: var(--success-color, #43a047); }
  .toggle.on .knob::after { left: 16px; }
  .toggle.off { border-color: var(--error-color, #db4437); color: var(--error-color, #db4437); }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .pill { font-size: .8rem; padding: 3px 10px; border-radius: 12px; }
  .pill.bad { background: rgba(219, 68, 55, .15); color: var(--error-color, #db4437); }
  .pill.warn { background: rgba(255, 152, 0, .15); color: var(--warning-color, #e68a00); }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 12px; }
  .stats div { display: flex; flex-direction: column; background: var(--secondary-background-color); border-radius: 10px; padding: 8px 10px; }
  .stats span { font-size: .75rem; color: var(--secondary-text-color); }
  .stats b { font-size: 1rem; font-weight: 500; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .apps { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; font-size: .8rem; color: var(--secondary-text-color); }
  .app { padding: 2px 8px; border-radius: 10px; border: 1px solid var(--divider-color); }
  .app b { color: var(--primary-text-color); font-weight: 500; }
  .actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .actions .restore { color: var(--primary-color); }
  .panel { margin-top: 10px; padding: 10px; border-radius: 12px; background: var(--secondary-background-color); }
  .lab { font-size: .75rem; color: var(--secondary-text-color); margin: 4px 0; text-transform: uppercase; letter-spacing: .04em; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  .go { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
`;

if (!customElements.get("family-wifi-card")) customElements.define("family-wifi-card", FamilyWifiCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "family-wifi-card")) {
  window.customCards.push({
    type: "family-wifi-card",
    name: "Family WiFi",
    description: "Pause, slow down and block websites per kid, with screen time and top apps.",
    preview: true,
  });
}
