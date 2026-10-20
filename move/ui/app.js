"use strict";
// Cherry Move's screens. The Go side (server.go) does the work; this page asks it to and shows /api/state.

const token = new URLSearchParams(location.search).get("t") || "";
const $ = (sel, root = document) => root.querySelector(sel);
const app = $("#app");
let S = {};            // latest /api/state
let page = "";
let drives = [], network = [];
const NORMAL = 9;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function human(n) {
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (n < 1000 || unit === "TB") return unit === "B" || unit === "KB" ? `${Math.round(n)} ${unit}` : `${n.toFixed(1)} ${unit}`;
    n /= 1000;
  }
}
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Cherry": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function dialog(title, text, buttons) {
  const d = $("#dialog");
  d.innerHTML = `<div class="dialog"><h3>${esc(title)}</h3><p>${esc(text)}</p><div class="stack"></div></div>`;
  for (const b of buttons) {
    const btn = document.createElement("button");
    btn.textContent = b.label;
    if (b.primary) btn.className = "primary";
    btn.onclick = () => { d.hidden = true; b.action && b.action(); };
    $(".stack", d).append(btn);
  }
  d.hidden = false;
}

function show(name, ...args) {
  page = name;
  $("#dialog").hidden = true;
  pages[name].render(...args);
  window.scrollTo(0, 0);
  // The second click of a double-click would otherwise land on the new page (e.g. untick a box).
  document.body.style.pointerEvents = "none";
  setTimeout(() => (document.body.style.pointerEvents = ""), 500);
}

// ------------------------------------------------------------------ choices made along the way
const choice = { storage: "usb", drive: "", nasType: "", carry: "usb", carryDrive: "", remoteKey: "" };

const STORAGE = [
  ["usb", "USB stick or external drive", "No internet needed. Cherry Move checks it fits first, and can spread over several sticks."],
  ["mine", "My Google Drive, OneDrive or MEGA", "Uses the free space in your own account."],
  ["nas", "A NAS or another computer at home", "A network drive or shared folder on your home network."],
];
const CARRY = [
  ["usb", "A USB stick", "Only the code goes on it (a few KB). Plug it into the new computer and your files come back by themselves."],
  ["phone", "My phone", "Scan a QR code to keep the code on your phone, then type it on the new computer."],
  ["code", "A code I'll keep safe", "Write down or print 34 letters and numbers."],
];
const SERVICES = [
  ["gdrive", "Google Drive", "Free accounts have 15 GB, shared with Gmail and Photos.", "Sign in with Google"],
  ["onedrive", "OneDrive", "Free accounts have 5 GB.", "Sign in with Microsoft"],
  ["mega", "MEGA", "Free accounts have 20 GB, but MEGA limits how much you can download each day, so getting a big move back can pause for a few hours. Accounts with two-step sign-in don't work yet.", "Sign in"],
];

function radios(group, options, selected, onPick) {
  return `<div class="list">${options.map(([id, title, sub]) => `
    <label class="row"><div class="main"><div class="title">${esc(title)}</div><div class="sub">${esc(sub)}</div></div>
    <input type="radio" name="${group}" value="${id}" ${id === selected ? "checked" : ""}></label>`).join("")}</div>`;
}

function bindRadios(group, onPick) {
  document.querySelectorAll(`input[name=${group}]`).forEach((r) => r.addEventListener("change", () => onPick(r.value)));
}

function fitText(d) {
  if (!d.fit) return { title: "", sub: `${human(d.usable)} free`, warn: false };
  const f = d.fit;
  if (f.sticks === 1) {
    const roomy = d.usable >= f.need * 1.15;
    return { title: roomy ? "It fits" : "It fits, just", sub: `Needs about ${human(f.need)}; ${human(d.usable)} free.`, warn: false };
  }
  return {
    title: "This stick is too small", warn: true,
    sub: `Needs about ${human(f.need)}; only ${human(d.usable)} free. Use one with at least ${human(d.bigger)} free, or ${f.sticks} sticks this size.`,
  };
}

let drivesKey = "";
// Redrawing replaces the stick menus, which loses a pick made while one is open, so polls only redraw on a change.
async function refreshDrives(onlyIfChanged) {
  try {
    const r = await api("/api/drives");
    drives = r.drives || [];
    network = r.network || [];
  } catch { /* the next poll tries again */ }
  const key = JSON.stringify([drives, network]);
  if (onlyIfChanged && key === drivesKey) return;
  drivesKey = key;
  pages[page]?.drives?.();
}

function driveSelect(id, selected) {
  if (!drives.length) return `<div class="sub">No USB stick found. Plug one in.</div>`;
  return `<select id="${id}">${drives.map((d) => `<option value="${esc(d.path)}" ${d.path === selected ? "selected" : ""}>${esc(d.title)}</option>`).join("")}</select>`;
}

// Links open in the browser, not in the app's window.
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-open]");
  if (!a) return;
  e.preventDefault();
  api("/api/open", { url: a.href }).catch(() => {});
});

// Asked by the app when its window is closed while files are being moved.
window.askClose = () => dialog("Stop moving your files?",
  "Cherry Move is still moving your files. If you close it now, the transfer stops and you'll need to start again.",
  [{ label: "Keep going", primary: true },
   { label: "Stop and close", action: () => { app.innerHTML = `<div class="hero"><h1>Stopping…</h1></div>`; api("/api/quit", {}).catch(() => {}); } }]);

function codeBox(code) {
  // The app's own window can't download files, so it asks the app to save it.
  const save = S.window ? `<button class="small" id="save">Save code as a file…</button>`
    : `<a href="/api/code.txt?t=${token}" download><button class="small">Save code as a file…</button></a>`;
  return `<div class="code" id="code">${esc(code)}</div>
    <div class="buttons"><button class="small" id="copy">Copy code</button>${save}
    <button class="small" id="print">Print code</button></div>`;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Some web views don't let pages use the clipboard API.
    const t = document.createElement("textarea");
    t.value = text;
    document.body.append(t);
    t.select();
    const ok = document.execCommand("copy");
    t.remove();
    if (!ok) throw new Error("copy");
  }
}

function bindCode() {
  $("#copy") && ($("#copy").onclick = async () => {
    try { await copyText($("#code").textContent); $("#copy").textContent = "Copied"; } catch { /* still selectable */ }
  });
  $("#save") && ($("#save").onclick = async () => {
    try {
      const r = await api("/api/save-code", {});
      if (r.saved) $("#save").textContent = "Saved";
    } catch (e) { dialog("Couldn't save the code", e.message, [{ label: "OK", primary: true }]); }
  });
  $("#print") && ($("#print").onclick = () => {
    const sheet = `<h2>Cherry Move transfer code</h2><p class="printcode">${esc($("#code").textContent)}</p>
      <p>On the new computer, open Cherry Move, choose “Get files from my old computer” and type this code.
      Anyone with it can get your files, so keep it safe.</p>`;
    if (S.window) {
      // The app's window prints the page itself; only this sheet shows on paper (style.css).
      let el = $("#printsheet");
      if (!el) { el = document.createElement("div"); el.id = "printsheet"; document.body.append(el); }
      el.innerHTML = sheet;
      return api("/api/print", {}).catch(() => {});
    }
    const w = window.open("", "_blank", "width=600,height=400");
    w.document.write(`<title>Cherry Move code</title><style>body{font-family:sans-serif;padding:40px}
      .printcode{font:bold 24px monospace}</style>${sheet}`);
    w.document.close();
    w.print();
  });
}

async function startSend(body) {
  try {
    await api("/api/send", body);
    S.sending = true;
    S.result = null;
    S.sendErr = "";
    show("progress");
  } catch (e) {
    dialog("Couldn't start", e.message, [{ label: "OK", primary: true }]);
  }
}

// ------------------------------------------------------------------ pages
const pages = {
  home: {
    render() {
      app.innerHTML = `<div class="hero"><img src="logo.svg" alt="">
        <h1>Move your files to Cherry OS</h1>
        <p>Cherry Move packs up your files on this computer, compressed and encrypted, so Cherry OS can bring
        them back on your new one. Nothing on this computer is changed or deleted.</p>
        <div class="stack"><button class="primary" id="go">Get started</button></div>
        <p style="margin-top:28px;font-size:.88rem">On the new computer, just plug in the stick while installing Cherry OS,
        or open Cherry Move there and choose “Get files from my old computer”.</p></div>`;
      $("#go").onclick = () => show("choose");
    },
  },

  choose: {
    shown: false,
    render() {
      this.shown = false;
      if (!S.catsReady) api("/api/scan", {}).catch(() => {});
      app.innerHTML = `<button class="back" id="back">‹ Back</button><h2>What should come with you?</h2>
        <p>Everything is compressed and encrypted on this computer before it leaves.</p><div id="cats"></div>
        <div class="actions"><button class="primary" id="next" disabled>Next</button></div>`;
      $("#back").onclick = () => show("home");
      $("#next").onclick = async () => {
        const ids = [...document.querySelectorAll("#cats input:checked")].map((c) => c.value);
        if (!ids.length) return dialog("Nothing chosen", "Tick at least one thing to bring.", [{ label: "OK", primary: true }]);
        try { await api("/api/plan", { ids }); show("where"); } catch (e) { dialog("Couldn't start", e.message, [{ label: "OK" }]); }
      };
      this.update();
    },
    update() {
      if (this.shown) return;
      if (!S.catsReady) {
        $("#cats").innerHTML = `<p><span class="spinner"></span>Looking through your folders…</p>`;
        return;
      }
      this.shown = true;
      const cats = (S.cats || []).filter((c) => c.files > 0);
      $("#cats").innerHTML = cats.length ? `<div class="list">${cats.map((c) => `
        <label class="row"><input type="checkbox" value="${esc(c.id)}" ${c.default ? "checked" : ""}>
        <div class="main"><div class="title">${esc(c.label)}</div>
        <div class="sub">${human(c.size)} · ${plural(c.files, "file")}${c.note ? " · " + esc(c.note) : ""}</div></div></label>`).join("")}</div>`
        : `<p>There's nothing in your folders to bring.</p>`;
      $("#next").disabled = !cats.length;
    },
  },

  where: {
    render() {
      app.innerHTML = `<button class="back" id="back">‹ Back</button><h2>Where should your files go?</h2>
        <p id="desc"></p>${radios("storage", STORAGE, choice.storage)}
        <div id="usb"></div><div id="mine" hidden></div><div id="nas" hidden></div>
        <div class="actions"><button class="primary" id="next" disabled>Working out the size…</button></div>`;
      $("#back").onclick = () => { api("/api/cancel", {}); show("choose"); };
      bindRadios("storage", (v) => { choice.storage = v; this.sections(); });
      this.renderMine();
      this.renderNas();
      $("#next").onclick = () => this.next();
      this.sections();
      this.update();
      refreshDrives();
    },
    sections() {
      $("#usb").hidden = choice.storage !== "usb";
      $("#mine").hidden = choice.storage !== "mine";
      $("#nas").hidden = choice.storage !== "nas";
      this.drives();
    },
    drives() {
      if (choice.storage !== "usb") return;
      if (!drives.find((d) => d.path === choice.drive)) choice.drive = drives[0]?.path || "";
      const d = drives.find((x) => x.path === choice.drive);
      const ft = d ? fitText(d) : null;
      $("#usb").innerHTML = `<h2>Which USB stick?</h2><div class="list">
        <div class="row"><div class="main"><div class="title">USB stick</div><div class="sub">${plural(drives.length, "stick")} found</div></div>${driveSelect("drive", choice.drive)}</div>
        ${ft && ft.title ? `<div class="row"><div class="main"><div class="title ${ft.warn ? "warn" : ""}">${ft.warn ? "⚠ " : "✓ "}${esc(ft.title)}</div><div class="sub">${esc(ft.sub)}</div></div></div>` : ""}</div>`;
      $("#drive") && ($("#drive").onchange = (e) => { choice.drive = e.target.value; this.drives(); });
    },
    renderMine() {
      const opts = SERVICES.map(([id, title]) => `<option value="${id}">${esc(title)}</option>`).join("");
      $("#mine").innerHTML = `<h2>Your account</h2><div class="list">
        <div class="field"><label for="service">Service</label><select id="service">${opts}</select></div>
        <div class="field mega"><label for="mega-user">Email</label><input type="email" id="mega-user" autocomplete="username"></div>
        <div class="field mega"><label for="mega-pass">Password</label><input type="password" id="mega-pass" autocomplete="current-password"></div>
        <div class="note" id="service-note"></div>
        <div class="row"><div class="main"><div class="title" id="mine-status">Not signed in</div><div class="sub" id="mine-sub"></div></div>
        <button class="primary small" id="mine-go">Sign in</button></div></div>`;
      const sync = () => {
        const s = SERVICES.find((x) => x[0] === $("#service").value);
        document.querySelectorAll("#mine .mega").forEach((e) => (e.hidden = s[0] !== "mega"));
        $("#service-note").textContent = s[2];
        $("#mine-go").textContent = s[3];
      };
      $("#service").onchange = () => { sync(); choice.remoteKey = ""; };
      document.querySelectorAll("#mine input").forEach((i) => (i.oninput = () => { choice.remoteKey = ""; }));
      sync();
      $("#mine-go").onclick = () => {
        const kind = $("#service").value;
        const settings = kind === "mega" ? { user: $("#mega-user").value.trim() } : {};
        if (kind === "mega" && !settings.user) return ($("#mine-sub").textContent = "Type your MEGA email and password.");
        this.connect("mine", { kind, settings, password: $("#mega-pass").value });
      };
    },
    nasTypes() {
      return S.nativeSMB
        ? [["folder", "Windows shared folder (\\\\NAS\\share)"], ["sftp", "SSH / SFTP"], ["webdav", "WebDAV"]]
        : [["smb", "Windows / Samba shared folder (SMB)"], ["sftp", "SSH / SFTP"], ["webdav", "WebDAV"]];
    },
    renderNas() {
      const types = this.nasTypes();
      if (!types.find((t) => t[0] === choice.nasType)) choice.nasType = types[0][0];
      $("#nas").innerHTML = `<h2>Your NAS</h2><div class="list">
        <div class="field"><label for="nas-type">Connect with</label><select id="nas-type">${types.map(([id, t]) => `<option value="${id}" ${id === choice.nasType ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
        <div id="nas-fields"></div>
        <div class="note">Any network drive: a Synology/QNAP NAS, a Windows shared folder, or a computer running SSH.</div>
        <div class="row"><div class="main"><div class="title" id="nas-status">Not connected</div><div class="sub" id="nas-sub"></div></div>
        <button class="primary small" id="nas-go">Connect</button></div></div>`;
      choice.insecure = false;
      $("#nas-type").onchange = (e) => {
        choice.nasType = e.target.value; choice.remoteKey = ""; choice.insecure = false;
        $("#nas-go").textContent = "Connect";
        this.nasFields();
      };
      this.nasFields();
      $("#nas-go").onclick = () => {
        const v = (id) => ($("#" + id)?.value || "").trim();
        const t = choice.nasType;
        if (t === "folder") {
          if (!v("nas-folder")) return ($("#nas-sub").textContent = "Type the network folder first, like \\\\NAS\\Backups.");
          return this.connect("nas", { kind: "folder", folder: v("nas-folder"), user: v("nas-user") });
        }
        if (!v("nas-host")) return ($("#nas-sub").textContent = "Type the NAS's address first.");
        if (t === "smb" && !v("nas-path")) return ($("#nas-sub").textContent = "Type the name of the shared folder to use.");
        const settings = t === "webdav" ? { type: t, url: v("nas-host"), user: v("nas-user") }
          : { type: t, host: v("nas-host"), path: v("nas-path"), user: v("nas-user") };
        if (choice.insecure) settings.insecure = true;
        this.connect("nas", { kind: "nas", settings, password: $("#nas-pass").value });
      };
    },
    nasFields() {
      const t = choice.nasType;
      const f = (id, label, type = "text", extra = "") => `<div class="field"><label for="${id}">${label}</label><input type="${type}" id="${id}" ${extra}></div>`;
      let html;
      if (t === "folder") {
        html = f("nas-folder", "Network folder (e.g. \\\\NAS\\Backups, or a mapped drive like Z:\\Backups)", "text", 'list="nas-list" placeholder="\\\\NAS\\Backups"') +
          `<datalist id="nas-list">${network.map((n) => `<option value="${esc(n)}">`).join("")}</datalist>` +
          f("nas-user", "Your username on the NAS (optional: the new computer fills it in for you)");
      } else if (t === "webdav") {
        html = f("nas-host", "Web address (e.g. https://nas.local:5006/)") + f("nas-user", "Username") + f("nas-pass", "Password", "password");
      } else {
        html = f("nas-host", "Address (name or IP, e.g. nas.local or 192.168.1.20)") +
          f("nas-path", t === "smb" ? "Shared folder, and a folder inside it if you like (e.g. Backups or Backups/Move)" : "Folder (e.g. Backups or /volume1/Backups)") +
          f("nas-user", "Username") + f("nas-pass", "Password", "password");
      }
      $("#nas-fields").innerHTML = html;
      document.querySelectorAll("#nas-fields input").forEach((i) => (i.oninput = () => {
        choice.remoteKey = "";
        if (choice.insecure) { choice.insecure = false; $("#nas-go").textContent = "Connect"; }
      }));
    },
    async connect(where, body) {
      choice.remoteKey = where + JSON.stringify(body.settings || body.folder || "");
      try { await api("/api/connect", body); } catch (e) { $(`#${where}-sub`).textContent = e.message; }
    },
    remoteReady() {
      const where = choice.storage;
      return S.remote?.state === "ready" && choice.remoteKey.startsWith(where);
    },
    update() {
      const desc = $("#desc");
      if (!desc) return;
      if (S.planErr) desc.innerHTML = `<span class="error">${esc(S.planErr)}</span>`;
      else if (S.planning) {
        const p = S.planProgress || {};
        desc.innerHTML = `<span class="spinner"></span>Working out how big your files will be${p.total ? `… ${Math.floor((p.done * 100) / p.total)}%` : "…"}`;
      } else if (S.sizes && S.plan) {
        let t = `${human(S.plan.raw)} to move.`;
        if (S.plan.duplicates) t += ` ${plural(S.plan.duplicates, "duplicate file")} (${human(S.plan.saved)}) are only stored once.`;
        t += S.sizes.normal < S.plan.size * 0.9
          ? ` It packs down to about ${human(S.sizes.normal)}, with some room to spare.`
          : ` Photos and videos are already compressed, so it needs about ${human(S.sizes.normal)}, with some room to spare.`;
        desc.textContent = t;
        $("#next").disabled = false;
        $("#next").textContent = "Next";
      }
      for (const where of ["mine", "nas"]) {
        const status = $(`#${where}-status`), sub = $(`#${where}-sub`);
        if (!status || choice.storage !== where) continue;
        const r = S.remote || {};
        if (!choice.remoteKey.startsWith(where)) {
          status.textContent = where === "mine" ? "Not signed in" : "Not connected";
          if (r.state !== "failed") sub.textContent = "";
          continue;
        }
        if (r.state === "working") {
          status.innerHTML = `<span class="spinner"></span>${where === "mine" ? "Signing in…" : "Connecting…"}`;
          sub.innerHTML = esc(r.message || "") + (r.url ? ` <a href="${esc(r.url)}" data-open>Browser didn't open? Open the sign-in page</a>` : "");
        } else if (r.state === "ready") {
          status.innerHTML = `<span class="ok">✓</span> ${where === "mine" ? "Signed in" : "Connected"}`;
          sub.textContent = r.free >= 0 ? `${human(r.free)} free` : "Ready";
        } else if (r.state === "failed") {
          status.textContent = r.cert ? "Is this your NAS?" : where === "mine" ? "Couldn't sign in" : "Couldn't connect";
          sub.innerHTML = `<span class="error">${esc(r.message)}</span>`;
          if (r.cert && where === "nas" && !choice.insecure) {
            choice.insecure = true;
            $("#nas-go").textContent = "Connect anyway";
          }
        }
      }
    },
    next() {
      if (!S.sizes) return;
      const need = S.sizes.normal;
      if (choice.storage === "usb") {
        const d = drives.find((x) => x.path === choice.drive);
        if (!d) return dialog("No USB stick", "Plug in a USB stick first.", [{ label: "OK", primary: true }]);
        const f = d.fit;
        const go = () => startSend({ storage: "usb", drive: d.path, level: f.level });
        if (f.sticks === 1) return go();
        return dialog("This stick is too small",
          `Your files pack down to about ${human(f.need)}, but this stick only has ${human(d.usable)} free.\n\nUse a USB stick or drive with at least ${human(d.bigger)} free, or ${f.sticks} sticks this size. With several sticks, Cherry Move tells you when to swap them.`,
          [{ label: `Use ${f.sticks} sticks`, primary: true, action: go }, { label: "Choose another stick" }]);
      }
      if (!this.remoteReady()) {
        return dialog(choice.storage === "mine" ? "Sign in first" : "Connect first",
          choice.storage === "mine" ? "Sign in to your account so Cherry Move can save your files there." : "Connect to your NAS so Cherry Move can save your files there.",
          [{ label: "OK", primary: true }]);
      }
      const r = S.remote;
      if (r.free >= 0 && need > r.free) {
        return dialog(`Not enough space in ${r.title}`,
          `Your files pack down to about ${human(need)}, and there's ${human(r.free)} free. Free up some space or choose somewhere else.`,
          [{ label: "Try anyway", action: () => show("carry") }, { label: "Back", primary: true }]);
      }
      show("carry");
    },
  },

  carry: {
    render() {
      const title = S.remote?.title || "your storage";
      app.innerHTML = `<button class="back" id="back">‹ Back</button><h2>How will you find them on the new computer?</h2>
        <p>Your files will be in ${esc(title)}, encrypted. The new computer needs a code to find and unlock them.</p>
        ${radios("carry", CARRY, choice.carry)}<div id="carry-usb"></div>
        <div class="actions"><button class="primary" id="start">Start</button></div>`;
      $("#back").onclick = () => show("where");
      bindRadios("carry", (v) => { choice.carry = v; this.drives(); });
      $("#start").onclick = () => {
        if (choice.carry === "usb" && !choice.carryDrive) return dialog("No USB stick", "Plug in a USB stick first, or choose another way.", [{ label: "OK", primary: true }]);
        startSend({ storage: choice.storage === "nas" && choice.nasType === "folder" ? "folder" : "remote", level: NORMAL,
          carry: choice.carry, carryDrive: choice.carryDrive });
      };
      this.drives();
      refreshDrives();
    },
    drives() {
      const box = $("#carry-usb");
      if (!box) return;
      if (choice.carry !== "usb") { box.innerHTML = ""; return; }
      if (!drives.find((d) => d.path === choice.carryDrive)) choice.carryDrive = drives[0]?.path || "";
      box.innerHTML = `<h2>Which USB stick?</h2><div class="list"><div class="row"><div class="main"><div class="title">USB stick</div>
        <div class="sub">${plural(drives.length, "stick")} found</div></div>${driveSelect("carry-drive", choice.carryDrive)}</div></div>`;
      $("#carry-drive") && ($("#carry-drive").onchange = (e) => (choice.carryDrive = e.target.value));
    },
  },

  progress: {
    render() {
      app.innerHTML = `<div class="hero"><div class="icon-big">📦</div><h1>Packing your files</h1>
        <p id="what"></p><div style="width:100%"><div class="bar busy" id="bar"><div></div></div>
        <div class="filename" id="file">Getting ready…</div></div>
        <div class="stack"><button id="cancel">Cancel</button></div></div>`;
      $("#cancel").onclick = () => dialog("Stop moving files?", "The transfer isn't finished yet.",
        [{ label: "Keep going", primary: true }, { label: "Stop", action: () => api("/api/cancel", {}) }]);
      this.update();
    },
    update() {
      if (S.prompt) return show("prompt");
      if (!S.sending) {
        if (S.result) return show("done");
        if (S.sendErr === "cancelled") return show("where");
        if (S.sendErr) return show("failed");
      }
      $("#what").textContent = `Compressing, encrypting and ${S.sendWhat || "saving"}.`;
      const p = S.sendProgress || {};
      if (p.total) {
        $("#bar").classList.remove("busy");
        $("#bar > div").style.width = `${Math.min(100, (p.done * 100) / p.total)}%`;
        $("#file").textContent = `${Math.floor((p.done * 100) / p.total)}% · ${human(p.done)} of ${human(p.total)}${p.name ? " · " + p.name : ""}`;
      }
    },
  },

  prompt: {
    known: null,
    render() {
      this.known = null;
      const part = S.prompt.part;
      app.innerHTML = `<div class="hero"><div class="icon-big">💾</div><h1>Stick ${part - 1} is full</h1>
        <p>Unplug it and write “${part - 1}” on it. Then plug in stick ${part}. Anything already on that stick stays there.</p>
        <p class="error" id="problem"></p></div><h2>USB sticks plugged in</h2><div id="sticks"></div>
        <div class="stack"><button id="cancel">Cancel</button></div>`;
      $("#cancel").onclick = () => dialog("Stop moving files?", "The transfer isn't finished yet.",
        [{ label: "Keep going", primary: true }, { label: "Stop", action: () => api("/api/cancel", {}) }]);
      this.update();
      this.drives();
      refreshDrives();
    },
    update() {
      if (!S.prompt) return show(S.sending ? "progress" : S.result ? "done" : "failed");
      $("#problem").textContent = S.prompt.problem || "";
    },
    drives() {
      const key = (d) => `${d.path}|${d.label}|${d.size}`;
      const now = new Set(drives.map(key));
      if (this.known === null) this.known = now;
      else {
        for (const k of [...this.known]) if (!now.has(k)) this.known.delete(k);
        const fresh = drives.find((d) => !this.known.has(key(d)));
        drives.forEach((d) => this.known.add(key(d)));
        if (fresh) api("/api/stick", { path: fresh.path }).catch(() => {});
      }
      $("#sticks").innerHTML = drives.length ? `<div class="list">${drives.map((d, i) => `
        <div class="row"><div class="main"><div class="title">${esc(d.title)}</div><div class="sub">${human(d.free)} free</div></div>
        <button class="small" data-i="${i}">Use this one</button></div>`).join("")}</div>` : `<p>Plug in stick ${S.prompt?.part}.</p>`;
      document.querySelectorAll("#sticks button").forEach((b) => (b.onclick = () => api("/api/stick", { path: drives[b.dataset.i].path })));
    },
  },

  done: {
    render() {
      const r = S.result;
      const files = plural(r.files, "file");
      let html;
      if (r.storage === "usb") {
        const text = r.sticks > 1
          ? `${files} are spread over ${r.sticks} USB sticks. Plug stick 1 (write 1 to ${r.sticks} on them) into your new computer while installing or using Cherry OS, and your files come back automatically.`
          : `${files} are on the USB stick. Plug it into your new computer while installing or using Cherry OS, and your files come back automatically.`;
        html = `<div class="hero"><div class="icon-big ok">✓</div><h1>All done!</h1><p>${esc(text)}</p></div>
          <details><summary>Show the code as a backup</summary>${codeBox(S.code)}</details>`;
      } else {
        const where = r.remote?.title || "your storage";
        if (S.carry === "usb") {
          html = `<div class="hero"><div class="icon-big ok">✓</div><h1>All done!</h1><p>${esc(files)} are encrypted in ${esc(where)}. Plug the USB stick into your new computer while installing or using Cherry OS, and connect to ${esc(where)} when it asks.</p></div>
            <details><summary>Show the code as a backup</summary>${codeBox(S.code)}</details>`;
        } else if (S.carry === "phone") {
          html = `<div class="hero"><h1>Scan this with your phone</h1><p>Your phone's camera shows the code: save it in your notes or take a photo.
            On the new computer, choose “Get files from my old computer”, then “Type a code”. ${esc(files)} are encrypted in ${esc(where)}.</p>
            <img class="qr" src="/api/qr?t=${token}" alt="QR code"></div><details><summary>No camera? Show the code</summary>${codeBox(S.code)}</details>`;
        } else {
          html = `<div class="hero"><h1>Keep this code safe</h1><p>You'll type it on your new computer. Anyone with it can get your files, so don't share it.
            ${esc(files)} are encrypted in ${esc(where)}.</p></div>${codeBox(S.code)}`;
        }
      }
      if (r.skipped?.length) {
        html += `<details><summary class="error">${plural(r.skipped.length, "file")} couldn't be read (another user's, or open in another app) and stayed behind</summary>
          <div class="list">${r.skipped.slice(0, 30).map((p) => `<div class="row"><div class="sub">${esc(p)}</div></div>`).join("")}</div></details>`;
      }
      app.innerHTML = html + `<div class="stack"><button class="primary" id="finish">Finish</button></div>`;
      bindCode();
      $("#finish").onclick = async () => {
        await api("/api/quit", {}).catch(() => {});
        window.close();
        app.innerHTML = `<div class="hero"><img src="logo.svg" alt=""><h1>All set</h1><p>You can close this window now.</p></div>`;
      };
    },
  },

  failed: {
    render() {
      app.innerHTML = `<div class="hero"><div class="icon-big">⚠</div><h1>Couldn't finish packing your files</h1>
        <p class="error">${esc(S.sendErr || "Something went wrong.")}</p>
        <div class="stack"><button class="primary" id="again">Try again</button></div></div>`;
      $("#again").onclick = () => show("where");
    },
  },
};

async function poll() {
  try {
    S = await api("/api/state");
    pages[page]?.update?.();
  } catch { /* the app may have closed */ }
  setTimeout(poll, page === "progress" ? 400 : 700);
}

setInterval(() => { if (["where", "carry", "prompt"].includes(page)) refreshDrives(true); }, 2500);
api("/api/state").then((s) => { S = s; show("home"); poll(); }).catch(() => {
  app.innerHTML = `<div class="hero"><h1>Cherry Move isn't running</h1><p>Open it again from its icon.</p></div>`;
});
