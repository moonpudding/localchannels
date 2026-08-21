/* LocalChannels frontend — vanilla JS, no build step. */

/* Browser-stored preferences (theme, sort order, grid/list choice). Wrapped so
   a blocked or full localStorage degrades to defaults instead of throwing. */
function prefGet(key) {
  try { return localStorage.getItem("lc-" + key); } catch (e) { return null; }
}

function prefSet(key, value) {
  try { localStorage.setItem("lc-" + key, value); } catch (e) {}
}

const state = {
  config: {},
  view: "browse",
  path: "",
  tree: [],
  channel: null, // {folder, tags, key, current, remaining, poolSize} or {savedId, name}
  searchQuery: "",
  autoplay: false,
  sort: { field: "name", order: "asc" },
  selectMode: false,
  selected: new Set(),
  treeExpanded: new Set(),
  channels: [],
  theme: "mocha",
  page: 0,
  vrDebug: false,    // VR timing report — opt-in, see Settings > General > Debugging
  viewMode: "grid",   // "grid" (thumbnails) | "list"
};

// Set by the video page; the router calls it to flush playback progress on exit.
let leaveVideo = null;

/* ------------------------------------------------------------------ utils */
const api = {
  async get(url) {
    const r = await fetch(url);
    if (r.status === 401) { location.href = "/login"; throw new Error("Unauthorized"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401) { location.href = "/login"; throw new Error("Unauthorized"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: "DELETE" });
    if (r.status === 401) { location.href = "/login"; throw new Error("Unauthorized"); }
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  },
  async patch(url, body) {
    const r = await fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401) { location.href = "/login"; throw new Error("Unauthorized"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
};

const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c == null) return;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return node;
};

function fmtDuration(sec) {
  if (!sec && sec !== 0) return "—";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}
// Compact, fixed-width timestamp for table columns (locale dates vary in length
// and truncate). Falls back to an em dash when the value is missing.
function fmtDateShort(ts) {
  if (!ts) return "\u2014";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// hh:mm:ss.mmm — used where small runtime differences matter (duplicate finding).
function fmtDurationMs(sec) {
  if (sec == null) return "\u2014";
  const total = Math.max(0, Math.round(sec * 1000));
  const ms = total % 1000;
  const s = Math.floor(total / 1000) % 60;
  const m = Math.floor(total / 60000) % 60;
  const h = Math.floor(total / 3600000);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(s)}.${p(ms, 3)}`;
}

function fmtBytes(b) {
  if (!b) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
function fmtDate(ts) {
  if (!ts) return "Never";
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) +
    " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
function resLabel(w, h) {
  if (!h) return null;
  const map = { 2160: "4K", 1440: "1440p", 1080: "1080p", 720: "720p", 480: "480p", 360: "360p", 240: "240p" };
  return map[h] || `${h}p`;
}

const ICON = {
  film: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="18" rx="2"/><path d="M7 3v18M17 3v18M2 8h20M2 16h20"/></svg>`,
  folder: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  play: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
  next: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 4l10 8-10 8zM19 5v14"/></svg>`,
  close: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>`,
  reset: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>`,
  heart: `<svg viewBox="0 0 24 24"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 1 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6 9 17l-5-5"/></svg>`,
  tv: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="m17 2-5 5-5-5"/></svg>`,
  pencil: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>`,
  refresh: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>`,
  vr: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7h18a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-5.5a2 2 0 0 1-1.7-1l-.8-1.3a1.2 1.2 0 0 0-2 0l-.8 1.3a2 2 0 0 1-1.7 1H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/></svg>`,
  expand: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M16 21h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>`,
  sparkle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l1.9 4.6 4.6 1.9-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>`,
  tag: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.6 13.4 12 22l-9-9V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5" fill="currentColor" stroke="none"/></svg>`,
  gridView: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`,
  listView: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>`,
  download: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>`,
};

let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

/* --------------------------------------------------------------- rendering */
const main = () => document.getElementById("main");

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function toggleFavorite(v, btn) {
  const newVal = !v.favorite;
  try {
    await api.post(`/api/video/${v.id}/favorite`, { value: newVal });
    v.favorite = newVal;
    if (btn) {
      btn.classList.toggle("on", newVal);
      btn.title = newVal ? "Remove from favorites" : "Add to favorites";
    }
    toast(newVal ? "Added to favorites" : "Removed from favorites");
    if (state.view === "favorites" && !newVal && btn) {
      // Remove the card from the favorites grid immediately.
      const card = btn.closest(".card, .list-row");
      if (card) card.remove();
    }
  } catch (e) {
    toast("Could not update favorite");
  }
}

function videoCard(v) {
  const res = resLabel(v.width, v.height);
  const selectable = state.selectMode;
  const selected = state.selected.has(v.id);
  const thumb = v.has_thumb
    ? el("img", { src: `/api/thumb/${v.id}`, loading: "lazy", alt: "" })
    : el("div", { class: "placeholder", html: ICON.film });
  const heart = el("button", {
    class: "heart" + (v.favorite ? " on" : ""),
    title: v.favorite ? "Remove from favorites" : "Add to favorites",
    html: ICON.heart,
    onClick: (ev) => { ev.stopPropagation(); toggleFavorite(v, ev.currentTarget); },
  });
  const wrap = el("div", { class: "thumb-wrap" }, [
    thumb,
    selectable ? el("div", { class: "select-check" + (selected ? " on" : "") },
      selected ? [el("span", { html: ICON.check })] : []) : null,
    selectable ? null : heart,
    v.vr ? el("span", { class: "badge vr" }, "VR") : null,
    v.duration ? el("span", { class: "badge" }, fmtDuration(v.duration)) : null,
    res ? el("span", { class: "badge res" }, res) : null,
    v.watched
      ? el("span", { class: "badge watched" }, [el("span", { html: ICON.check }), "Watched"])
      : null,
    (!v.watched && v.position > 0 && v.duration)
      ? el("div", { class: "progress-bar" }, [
          el("div", { style: `width:${Math.min(100, (v.position / v.duration) * 100)}%` }),
        ])
      : null,
  ]);
  const sub = el("div", { class: "card-sub" }, [
    v.fps ? el("span", {}, `${Math.round(v.fps)}fps`) : null,
    v.vcodec ? el("span", { class: "dot" }, "•") : null,
    v.vcodec ? el("span", {}, v.vcodec) : null,
    v.size ? el("span", { class: "dot" }, "•") : null,
    v.size ? el("span", {}, fmtBytes(v.size)) : null,
  ]);
  const played = v.last_played
    ? el("div", { class: "card-played" }, `Watched ${fmtDate(v.last_played)}`)
    : null;
  const card = el("div", {
    class: "card" + (selectable ? " selectable" : "") + (selected ? " selected" : ""),
  }, [
    wrap,
    el("div", { class: "card-meta" }, [
      el("div", { class: "card-title", title: v.title }, v.title),
      sub,
      played,
    ]),
  ]);
  card.addEventListener("click", () => {
    if (state.selectMode) toggleSelect(v.id, card);
    else navigate("video", v.id);
  });
  return card;
}

function toggleSelect(id, card) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  const sel = state.selected.has(id);
  card.classList.toggle("selected", sel);
  const check = card.querySelector(".select-check");
  if (check) {
    check.classList.toggle("on", sel);
    check.innerHTML = sel ? ICON.check : "";
  }
  updateSelectCount();
}

function updateSelectCount() {
  const c = document.getElementById("st-count");
  if (c) c.textContent = `${state.selected.size} selected`;
}

function crumbs(path) {
  const parts = path ? path.split("/") : [];
  const items = [el("a", { onClick: () => go("browse", "") }, "Library")];
  let acc = "";
  parts.forEach((p) => {
    acc = acc ? `${acc}/${p}` : p;
    const target = acc;
    items.push(el("span", { class: "sep" }, "/"));
    items.push(el("a", { onClick: () => go("browse", target) }, p));
  });
  return el("div", { class: "crumbs" }, items);
}

const SORT_FIELDS = [
  ["name", "Name"], ["added", "Date added"], ["file", "File date"], ["length", "Length"],
  ["resolution", "Resolution"], ["size", "File size"],
];

function sortBar(onChange, extra) {
  const select = el("select", {
    onChange: (e) => { state.sort.field = e.target.value; persistSort(); onChange(); },
  }, SORT_FIELDS.map(([v, label]) =>
    el("option", { value: v, ...(state.sort.field === v ? { selected: "" } : {}) }, label)));
  const orderBtn = el("button", {
    class: "order-btn",
    title: state.sort.order === "asc" ? "Ascending" : "Descending",
    html: state.sort.order === "asc"
      ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M6 11l6-6 6 6"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M6 13l6 6 6-6"/></svg>`,
    onClick: () => { state.sort.order = state.sort.order === "asc" ? "desc" : "asc"; persistSort(); onChange(); },
  });
  return el("div", { class: "sortbar" }, [
    el("span", { class: "sb-label" }, "Sort"),
    select, orderBtn,
    el("div", { class: "spacer" }),
    viewToggle(onChange),
    extra || null,
  ]);
}

function persistSort() {
  prefSet("sort", JSON.stringify(state.sort));
}

// Shared select toolbar for mass tagging. `refresh` re-renders the current view.
function selectToolbar(refresh) {
  let allTags = [];
  api.get("/api/tags").then((d) => {
    allTags = d.tags;
    dl.innerHTML = "";
    allTags.forEach((t) => dl.appendChild(el("option", { value: t.name })));
  }).catch(() => {});
  const dl = el("datalist", { id: "bulk-tag-suggest" });
  const input = el("input", {
    type: "text", placeholder: "tag1, tag2…", list: "bulk-tag-suggest", autocomplete: "off",
  });
  const apply = async () => {
    const tags = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!state.selected.size) { toast("Select some videos first"); return; }
    if (!tags.length) { toast("Type a tag to apply"); return; }
    try {
      await api.post("/api/videos/tags", { video_ids: [...state.selected], tags });
      toast(`Tagged ${state.selected.size} video${state.selected.size === 1 ? "" : "s"}`);
      state.selectMode = false; state.selected.clear();
      loadTree();
      refresh();
    } catch (e) { toast("Could not apply tags"); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); apply(); } });
  const bulkDelete = async () => {
    if (!state.selected.size) { toast("Select some videos first"); return; }
    const n = state.selected.size;
    if (!confirm(`Delete ${n} video${n === 1 ? "" : "s"} from disk?\n\nThe files will be permanently removed. This cannot be undone.`)) return;
    try {
      await api.post("/api/videos/delete", { video_ids: [...state.selected] });
      toast(`Deleted ${n} video${n === 1 ? "" : "s"}`);
      state.selectMode = false; state.selected.clear();
      loadTree(); loadVRNav();
      refresh();
    } catch (e) { toast("Could not delete videos"); }
  };
  return el("div", { class: "select-toolbar" }, [
    el("span", { class: "st-count", id: "st-count" }, `${state.selected.size} selected`),
    el("button", { class: "btn", onClick: () => { selectAllVisible(); } }, "Select all"),
    el("button", { class: "btn", onClick: () => { state.selected.clear(); refresh(); } }, "Clear"),
    el("div", { class: "spacer" }),
    input, dl,
    el("button", { class: "btn primary", onClick: apply }, "Apply tags"),
    el("button", { class: "btn ghost", style: "color:var(--danger)", onClick: bulkDelete },
      [el("span", { html: ICON.trash }), "Delete"]),
    el("button", { class: "btn ghost", onClick: () => { state.selectMode = false; state.selected.clear(); refresh(); } }, "Done"),
  ]);
}

function selectAllVisible() {
  document.querySelectorAll(".card, .list-row").forEach((card) => card.classList.add("selected"));
  // Mark every currently rendered card as selected.
  window.__lastGridIds?.forEach((id) => state.selected.add(id));
  document.querySelectorAll(".card .select-check, .list-row .select-check").forEach((ch) => {
    ch.classList.add("on"); ch.innerHTML = ICON.check;
  });
  updateSelectCount();
}

function videoGrid(videos) {
  window.__lastGridIds = videos.map((v) => v.id);
  if (state.viewMode === "list") return videoList(videos);
  const g = el("div", { class: "grid" });
  videos.forEach((v) => g.appendChild(videoCard(v)));
  return g;
}

/* ------------------------------------------------------------- list view */

// Columns, in render order. `cls` picks up the monospace numeric styling.
const LIST_COLUMNS = [
  ["Name", ""], ["Folder", "lc-path"], ["Size", "lc-num"], ["File date", "lc-num"],
  ["Added", "lc-num"], ["Resolution", "lc-num"], ["Duration", "lc-num"], ["FPS", "lc-num"],
];

function listRow(v) {
  const selectable = state.selectMode;
  const selected = state.selected.has(v.id);

  const heart = el("button", {
    class: "lc-heart" + (v.favorite ? " on" : ""),
    title: v.favorite ? "Remove from favorites" : "Add to favorites",
    html: ICON.heart,
    onClick: (ev) => { ev.stopPropagation(); toggleFavorite(v, ev.currentTarget); },
  });

  const name = el("div", { class: "lc-name" }, [
    selectable
      ? el("span", { class: "select-check" + (selected ? " on" : "") },
          selected ? [el("span", { html: ICON.check })] : [])
      : heart,
    el("span", { class: "lc-title", title: v.filename }, v.title),
    v.vr ? el("span", { class: "lc-tag" }, "VR") : null,
    v.watched ? el("span", { class: "lc-tag watched", title: "Watched" }, [el("span", { html: ICON.check })]) : null,
  ]);

  const res = resLabel(v.width, v.height);
  const row = el("div", {
    class: "list-row" + (selectable ? " selectable" : "") + (selected ? " selected" : ""),
  }, [
    name,
    el("div", { class: "lc-path", title: v.folder || "/" }, v.folder || "/"),
    el("div", { class: "lc-num" }, v.size ? fmtBytes(v.size) : "—"),
    el("div", { class: "lc-num", title: v.mtime ? fmtDate(v.mtime) : "" }, fmtDateShort(v.mtime)),
    el("div", { class: "lc-num", title: v.added_at ? fmtDate(v.added_at) : "" }, fmtDateShort(v.added_at)),
    el("div", { class: "lc-num" }, v.width && v.height ? `${v.width}×${v.height}${res ? ` (${res})` : ""}` : "—"),
    el("div", { class: "lc-num", title: v.duration ? fmtDurationMs(v.duration) : "" },
      v.duration ? fmtDuration(v.duration) : "—"),
    el("div", { class: "lc-num" }, v.fps ? `${Math.round(v.fps)}` : "—"),
  ]);
  row.addEventListener("click", () => {
    if (state.selectMode) toggleSelect(v.id, row);
    else navigate("video", v.id);
  });
  return row;
}

function videoList(videos) {
  const inner = el("div", { class: "vlist-inner" }, [
    el("div", { class: "list-head" },
      LIST_COLUMNS.map(([label, cls]) => el("div", { class: cls }, label))),
  ]);
  videos.forEach((v) => inner.appendChild(listRow(v)));
  return el("div", { class: "vlist" }, [inner]);
}

// Flips thumbnails ⇄ list. The choice is remembered per browser and applies to
// every video listing.
function viewToggle(refresh) {
  const isList = state.viewMode === "list";
  return el("button", {
    class: "order-btn",
    title: isList ? "Switch to thumbnail view" : "Switch to list view",
    html: isList ? ICON.gridView : ICON.listView,
    onClick: () => {
      state.viewMode = isList ? "grid" : "list";
      prefSet("view", state.viewMode);
      refresh();
    },
  });
}

// Minimal bar for views that have no sort controls of their own.
function viewBar(refresh) {
  return el("div", { class: "sortbar" }, [
    el("div", { class: "spacer" }),
    viewToggle(refresh),
  ]);
}

async function renderBrowse() {
  setActiveNav("browse", state.path);
  const c = main();
  c.innerHTML = "";
  c.appendChild(await indexBanner());
  const rescanNow = el("button", {
    class: "btn", title: "Scan the library folder for new or removed files",
    onClick: async (e) => {
      const b = e.currentTarget;
      b.disabled = true;
      try { await reindex(); } catch (err) { toast("Could not start rescan"); b.disabled = false; }
    },
  }, [el("span", { html: ICON.refresh }), "Rescan"]);
  c.appendChild(el("div", { class: "page-head" }, [
    el("h1", { class: "page-title" }, state.path ? state.path.split("/").pop() : "Library"),
    el("div", { class: "spacer" }),
    rescanNow,
  ]));
  c.appendChild(crumbs(state.path));

  let data;
  try {
    data = await api.get(
      `/api/browse?path=${encodeURIComponent(state.path)}&sort=${state.sort.field}&order=${state.sort.order}`
    );
  } catch (e) {
    c.appendChild(el("div", { class: "empty" }, [el("h2", {}, "Could not load folder")]));
    return;
  }

  if (data.folders.length === 0 && data.videos.length === 0) {
    c.appendChild(emptyLibrary());
    return;
  }

  if (data.folders.length) {
    c.appendChild(el("div", { class: "section-title" }, "Folders"));
    const g = el("div", { class: "grid" });
    data.folders.forEach((f) => {
      g.appendChild(el("div", { class: "folder-tile", onClick: () => go("browse", f.path) }, [
        el("span", { html: ICON.folder }),
        el("div", {}, [
          el("div", { class: "ft-name", title: f.name }, f.name),
          el("div", { class: "ft-count" }, `${f.count} video${f.count === 1 ? "" : "s"}`),
        ]),
      ]));
    });
    c.appendChild(g);
  }

  if (data.videos.length) {
    if (state.selectMode) {
      c.appendChild(selectToolbar(renderBrowse));
    } else {
      const selectBtn = el("button", {
        class: "btn", onClick: () => { state.selectMode = true; state.selected.clear(); renderBrowse(); },
      }, [el("span", { html: ICON.check }), "Select"]);
      c.appendChild(sortBar(renderBrowse, selectBtn));
    }
    c.appendChild(el("div", { class: "section-title" }, `Videos (${data.videos.length})`));
    c.appendChild(videoGrid(data.videos));
  }
}

// A lightweight WebGL panorama viewer for 360°/180° (and basic fisheye) video.
// Drag to look around, scroll to zoom. Handles mono / side-by-side / top-bottom
// stereo by sampling one eye. Returns a cleanup function.
function mountVRViewer(host, video, v, id) {
  const stage = el("div", { class: "vr-stage" });
  const canvas = el("canvas", { class: "vr-canvas" });
  // In magic-window mode the canvas is what's shown, so the element itself is
  // parked out of sight. While immersed, though, the 2D page isn't rendered at
  // all and browsers throttle — or outright disable — the video track of an
  // element they consider invisible, which stalls frames while audio keeps
  // running. So the element gets a real size and full opacity for the duration
  // of the XR session (nothing is on screen to be disturbed by it).
  const VIDEO_STYLE_2D = "position:absolute;left:0;top:0;width:1px;height:1px;opacity:0;pointer-events:none";
  const VIDEO_STYLE_XR = "position:absolute;inset:0;width:100%;height:100%;object-fit:contain;pointer-events:none";
  video.setAttribute("style", VIDEO_STYLE_2D);
  video.removeAttribute("controls");
  video.playsInline = true;
  try { video.disableRemotePlayback = true; } catch (e) {}
  // "metadata" is right for a library grid; a player wants to run ahead.
  video.preload = "auto";
  stage.appendChild(video);
  stage.appendChild(canvas);

  // xrCompatible up front matters: calling makeXRCompatible() later on a
  // context that wasn't created for XR is explicitly allowed to drop and
  // restore the context. Every GL object (program, buffer, texture) dies with
  // it, and the frame loop then draws nothing forever while still pumping
  // frames — a black headset and a browser that has to be force-quit.
  const GL_OPTS = {
    xrCompatible: true, alpha: false, antialias: false, depth: false,
    stencil: false, preserveDrawingBuffer: false, powerPreference: "high-performance",
  };
  let gl = canvas.getContext("webgl", GL_OPTS) || canvas.getContext("experimental-webgl", GL_OPTS);
  if (!gl) {
    video.removeAttribute("style");
    video.setAttribute("controls", "");
    host.appendChild(el("div", { class: "player-shell" }, [video]));
    toast("WebGL isn't available — showing the flat video");
    return () => {};
  }

  let projection = v.vr_projection || "eq180";  // eq360 | eq180 | fisheye
  let layout = v.vr_layout || "mono";           // mono | sbs | tb
  let yaw = 0, pitch = 0, fov = (75 * Math.PI) / 180;

  const VS = "attribute vec2 aPos; varying vec2 vUv; void main(){ vUv=aPos*0.5+0.5; gl_Position=vec4(aPos,0.0,1.0); }";
  const FS = `precision highp float; varying vec2 vUv;
    uniform float uYaw,uPitch,uFov,uAspect,uZoom; uniform int uMode; uniform int uUseXR;
    uniform mat4 uInvProj,uInvView;
    uniform vec2 uUvScale,uUvOffset; uniform sampler2D uTex;
    #define PI 3.141592653589793
    void main(){
      vec2 ndc = vUv*2.0-1.0;
      vec3 d;
      if(uUseXR==1){
        // Headset path: unproject through the eye's (possibly asymmetric)
        // projection, then rotate by the head pose. uInvView is rotation-only.
        // uZoom narrows the ray bundle -> binocular-style magnification.
        vec4 p = uInvProj * vec4(ndc, -1.0, 1.0);
        vec3 vd = p.xyz / p.w;
        vd.xy /= uZoom;
        d = normalize((uInvView * vec4(vd, 0.0)).xyz);
      } else {
        // Magic-window path: mouse-look yaw/pitch with a symmetric fov.
        float t = tan(uFov*0.5);
        d = normalize(vec3(ndc.x*t*uAspect, ndc.y*t, -1.0));
        float cp=cos(uPitch), sp=sin(uPitch);
        d = vec3(d.x, cp*d.y - sp*d.z, sp*d.y + cp*d.z);
        float cy=cos(uYaw), sy=sin(uYaw);
        d = vec3(cy*d.x + sy*d.z, d.y, -sy*d.x + cy*d.z);
      }
      vec2 uv;
      if(uMode==2){
        if(d.z>0.0){ gl_FragColor=vec4(0.0,0.0,0.0,1.0); return; }
        float theta=acos(clamp(-d.z,-1.0,1.0)); float r=theta/(PI*0.5)*0.5;
        float phi=atan(d.y,d.x); uv=vec2(0.5+r*cos(phi), 0.5+r*sin(phi));
      } else {
        float lon=atan(d.x,-d.z); float lat=asin(clamp(d.y,-1.0,1.0));
        float vv=0.5-lat/PI; float u;
        if(uMode==0){ u=0.5+lon/(2.0*PI); }
        else { if(abs(lon)>PI*0.5){ gl_FragColor=vec4(0.0,0.0,0.0,1.0); return; } u=0.5+lon/PI; }
        uv=vec2(u,vv);
      }
      uv=uv*uUvScale+uUvOffset;
      gl_FragColor=texture2D(uTex, uv);
    }`;

  const compile = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
  let prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  gl.useProgram(prog);

  let buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  let tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  // RGBA + no colorspace conversion lets the browser use its fast
  // (GPU-accelerated) video-to-texture path; RGB forces a slow CPU repack.
  gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);

  // Uploading an 8K frame with texImage2D reallocates the whole texture every
  // time (~120 MB for 7680x3840 RGBA). Allocate once, then texSubImage2D into
  // the existing storage — same pixels, none of the reallocation churn.
  let texW = 0, texH = 0, texUploads = 0;
  const uploadFrame = () => {
    if (video.readyState < 2) return;
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    try {
      if (w !== texW || h !== texH) {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
        texW = w; texH = h;
      } else {
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, video);
      }
      texUploads++;
    } catch (e) { texW = 0; texH = 0; }
  };

  const U = (n) => gl.getUniformLocation(prog, n);
  let uYaw, uPitch, uFov, uAspect, uMode, uUvScale, uUvOffset, uUseXR,
    uInvProj, uInvView, uZoom;
  const cacheUniforms = () => {
    uYaw = U("uYaw"); uPitch = U("uPitch"); uFov = U("uFov"); uAspect = U("uAspect");
    uMode = U("uMode"); uUvScale = U("uUvScale"); uUvOffset = U("uUvOffset");
    uUseXR = U("uUseXR"); uInvProj = U("uInvProj"); uInvView = U("uInvView");
    uZoom = U("uZoom");
  };
  cacheUniforms();

  // Rebuild every GL object from scratch. Used after a context restore, where
  // the old program/buffer/texture handles are all invalid.
  const rebuildGL = () => {
    prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    gl.useProgram(prog);
    buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
    const a = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(a);
    gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
    tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    texW = 0; texH = 0;
    cacheUniforms();
  };

  let glLost = false, glLostAt = 0;
  canvas.addEventListener("webglcontextlost", (ev) => {
    ev.preventDefault(); // required, or the context is never restored
    glLost = true;
    if (diag) diag.notes.push("WebGL context lost");
    console.warn("[vr] WebGL context lost");
  });
  canvas.addEventListener("webglcontextrestored", () => {
    try { rebuildGL(); glLost = false; if (diag) diag.notes.push("WebGL context restored"); }
    catch (e) { console.error("[vr] context restore failed", e); }
  });

  const modeOf = () => (projection === "eq360" ? 0 : projection === "eq180" ? 1 : 2);
  const scaleOf = () => (layout === "sbs" ? [0.5, 1] : layout === "tb" ? [1, 0.5] : [1, 1]);

  let raf = 0;
  const draw = () => {
    // Schedule the next frame FIRST: the viewer can be mounted before the
    // page fragment is attached to the document, and bailing without
    // rescheduling would kill the loop permanently (blank canvas).
    raf = requestAnimationFrame(draw);
    if (!canvas.isConnected || glLost) return; // not attached, or context gone
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;            // attached but not laid out yet
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    uploadFrame();
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform1i(uUseXR, 0);
    gl.uniform1f(uYaw, yaw); gl.uniform1f(uPitch, pitch); gl.uniform1f(uFov, fov);
    gl.uniform1f(uAspect, canvas.width / canvas.height);
    gl.uniform1i(uMode, modeOf());
    const sc = scaleOf(); gl.uniform2f(uUvScale, sc[0], sc[1]); gl.uniform2f(uUvOffset, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  };

  let dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener("pointerdown", (e) => { dragging = true; lastX = e.clientX; lastY = e.clientY; canvas.setPointerCapture(e.pointerId); showBar(); });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const k = fov / canvas.clientHeight;
    yaw -= (e.clientX - lastX) * k;
    pitch -= (e.clientY - lastY) * k;
    pitch = Math.max(-Math.PI / 2 + 0.01, Math.min(Math.PI / 2 - 0.01, pitch));
    lastX = e.clientX; lastY = e.clientY;
  });
  const endDrag = () => { dragging = false; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    fov = Math.max(30 * Math.PI / 180, Math.min(110 * Math.PI / 180, fov + (e.deltaY > 0 ? 0.05 : -0.05)));
  }, { passive: false });

  const playBtn = el("button", { class: "vr-ic", html: ICON.play, title: "Play/Pause" });
  playBtn.addEventListener("click", () => { if (video.paused) video.play(); else video.pause(); });
  const syncPlay = () => { playBtn.innerHTML = video.paused ? ICON.play : `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>`; };
  video.addEventListener("play", syncPlay); video.addEventListener("pause", syncPlay);

  const seek = el("input", { type: "range", class: "vr-seek", min: "0", max: "100", value: "0", step: "0.1" });
  const timeLbl = el("span", { class: "vr-time" }, "0:00 / 0:00");
  let seeking = false;
  seek.addEventListener("input", () => { seeking = true; if (video.duration) video.currentTime = (seek.value / 100) * video.duration; });
  seek.addEventListener("change", () => { seeking = false; });
  video.addEventListener("timeupdate", () => {
    if (!video.duration) return;
    if (!seeking) seek.value = String((video.currentTime / video.duration) * 100);
    timeLbl.textContent = `${fmtDuration(video.currentTime)} / ${fmtDuration(video.duration)}`;
  });

  const saveVR = debounce(() => { api.post(`/api/video/${id}/vr`, { vr: true, projection, layout }).catch(() => {}); }, 600);
  const projLabel = () => (projection === "eq360" ? "360°" : projection === "eq180" ? "180°" : "Fisheye");
  const projBtn = el("button", {}, projLabel());
  projBtn.addEventListener("click", () => {
    projection = projection === "eq360" ? "eq180" : projection === "eq180" ? "fisheye" : "eq360";
    projBtn.textContent = projLabel(); v.vr_projection = projection; saveVR(); showBar();
  });
  const layoutLabel = () => (layout === "mono" ? "Mono" : layout === "sbs" ? "SBS 3D" : "TB 3D");
  const layoutBtn = el("button", {}, layoutLabel());
  layoutBtn.addEventListener("click", () => {
    layout = layout === "mono" ? "sbs" : layout === "sbs" ? "tb" : "mono";
    layoutBtn.textContent = layoutLabel(); v.vr_layout = layout; saveVR(); showBar();
  });
  // "auto" prefers the compositor media layer; "webgl" forces our own shader.
  // Exposed because the two paths pace video frames very differently and only
  // hardware can say which is smoother for a given file.
  let rendererPref = prefGet("vr-renderer") === "webgl" ? "webgl" : "auto";
  const reportBtn = el("button", { title: "Timing report from the last VR session", style: "display:none" }, "VR report");
  reportBtn.addEventListener("click", () => {
    reportPanel.style.display = reportPanel.style.display === "none" ? "" : "none";
  });
  const rendLabel = () => (rendererPref === "webgl" ? "VR: WebGL" : "VR: Auto");
  const rendBtn = el("button", { title: "Which renderer immersive VR uses (applies next time you enter VR)" }, rendLabel());
  rendBtn.addEventListener("click", () => {
    rendererPref = rendererPref === "auto" ? "webgl" : "auto";
    prefSet("vr-renderer", rendererPref);
    rendBtn.textContent = rendLabel();
    toast(rendererPref === "webgl"
      ? "Immersive VR will use the WebGL renderer next time"
      : "Immersive VR will use the compositor when available");
    showBar();
  });
  const recenterBtn = el("button", {}, "Recenter");
  recenterBtn.addEventListener("click", () => { yaw = 0; pitch = 0; fov = (75 * Math.PI) / 180; showBar(); });
  const fsBtn = el("button", { class: "vr-ic", html: ICON.expand, title: "Fullscreen" });
  fsBtn.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (stage.requestFullscreen) stage.requestFullscreen();
  });
  document.addEventListener("fullscreenchange", () => stage.classList.toggle("fs", document.fullscreenElement === stage));

  const bar = el("div", { class: "vr-bar" }, [playBtn, seek, timeLbl, projBtn, layoutBtn, rendBtn, reportBtn, recenterBtn, fsBtn]);

  // ── WebXR: true immersive playback on a headset (Quest browser, Wolvic,
  // desktop Chrome + PCVR, ...). Needs a secure context (HTTPS/localhost);
  // the button only appears when the browser reports headset support. ──
  let xrSession = null, xrRefSpace = null, xrBtn = null;
  // Fraction of the browser's recommended eye-buffer size used by the WebGL
  // fallback. Lower trades sharpness for headroom; 1 = recommended size.
  const XR_FB_SCALE = 0.8;

  // Compositor-side playback (WebXR media layers): the headset samples the
  // video element directly — no per-frame WebGL copies, so no stutter.
  let xrMediaLayer = null;
  // WebGL fallback: only re-upload the texture when the video actually has a
  // new frame (~24-30/s) instead of every XR frame (72-90/s).
  let xrNewFrame = true, rvfcId = 0, rvfcActive = false;
  let xrZoom = 1, xrSwitching = false, xrPathInfo = "";

  // ── Session diagnostics ───────────────────────────────────────────────
  // Nothing in the DOM is visible while immersed, so console logs and toasts
  // are useless for debugging on a headset. Instead every session records what
  // it did and the numbers get written out as a readable report on exit.
  let diag = null;
  const newDiag = () => ({
    started: Date.now(), path: "", frames: 0, longFrames: 0,
    frameTimes: [], lastFrameT: 0, firstFrameT: 0, uploads0: 0,
    waiting: 0, stalled: 0, ratechange: 0,
    vis: [], docVis: [], quality0: null, fbW: 0, fbH: 0, scale: 0,
    targetRate: 0, gotRate: 0, foveation: null, notes: [], rates: "", ratePlan: "", playT0: 0,
    vfCount: 0, vfWall: [], vfMedia: [], lastVfNow: 0, lastVfMedia: null,
    buf: [], bufAtWait: [], lastBufT: 0, vfClock: "",
  });
  const srcFps = Number(v.fps) || 0;
  const vq = () => {
    try { return video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null; }
    catch (e) { return null; }
  };
  // Seconds of decoded video sitting ahead of the playhead. If this runs to
  // near zero the pipeline is starving for data; if it stays deep while frames
  // still arrive late, the problem is downstream of delivery.
  const bufAhead = () => {
    try {
      const b = video.buffered, t = video.currentTime;
      for (let i = 0; i < b.length; i++) {
        if (b.start(i) <= t && t <= b.end(i)) return b.end(i) - t;
      }
    } catch (e) {}
    return 0;
  };
  const onWaiting = () => {
    if (!diag) return;
    diag.waiting++;
    diag.bufAtWait.push(bufAhead());
  };
  const onStalled = () => { if (diag) diag.stalled++; };
  const onDocVis = () => { if (diag) diag.docVis.push(document.visibilityState); };

  const fmtPct = (arr, p) => {
    if (!arr.length) return "-";
    const s = arr.slice().sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor(s.length * p))].toFixed(1);
  };

  const buildReport = () => {
    if (!diag) return "";
    const secs = (Date.now() - diag.started) / 1000;
    const q1 = vq();
    const L = [];
    L.push(`render path      : ${diag.path}   [renderer pref: ${rendererPref}]`);
    L.push(`session length   : ${secs.toFixed(1)}s`);
    L.push(`video            : ${video.videoWidth}x${video.videoHeight}  ${projection} / ${layout}`);
    L.push(`source fps       : ${srcFps ? srcFps.toFixed(3) : "unknown (not in metadata)"}`);
    // Use the MEDIAN frame interval, not the average. The session can be
    // blurred or hidden while the system menu or a refresh-rate change is in
    // flight, and those gaps drag an average far away from the rate actually
    // being displayed — the last report read 69.9/s while sitting at a steady
    // 90 Hz because of exactly that.
    const medMs = diag.frameTimes.length
      ? diag.frameTimes.slice().sort((a, b) => a - b)[Math.floor(diag.frameTimes.length / 2)] : 0;
    const xrFps = medMs > 0 ? 1000 / medMs : 0;
    L.push(`XR frames        : ${diag.frames}  (${xrFps.toFixed(1)}/s steady, median interval)`);
    if (srcFps) {
      const cad = xrFps / srcFps;
      L.push(`cadence          : ${cad.toFixed(3)} refreshes/frame` +
        (Math.abs(cad - Math.round(cad)) > 0.02 ? "  <-- UNEVEN, causes judder" : "  (even)"));
    }
    L.push(`refresh rate     : plan ${diag.ratePlan || "-"}, applied ${diag.targetRate || "none"}` +
      `, reported ${diag.gotRate ? diag.gotRate.toFixed(1) : "?"}, offered ${diag.rates || "?"}`);
    L.push(`  frame time ms  : p50 ${fmtPct(diag.frameTimes, 0.5)}  p95 ${fmtPct(diag.frameTimes, 0.95)}  p99 ${fmtPct(diag.frameTimes, 0.99)}`);
    L.push(`  long frames    : ${diag.longFrames} (>25ms)`);
    if (diag.quality0 && q1) {
      const playSecs = Math.max((Date.now() - (diag.playT0 || diag.started)) / 1000, 0.001);
      const dec = q1.totalVideoFrames - diag.quality0.totalVideoFrames;
      const drop = q1.droppedVideoFrames - diag.quality0.droppedVideoFrames;
      L.push(`decoded frames   : ${dec}  (${(dec / playSecs).toFixed(1)}/s over ${playSecs.toFixed(1)}s of playback)`);
      L.push(`dropped frames   : ${drop}`);
    } else {
      L.push(`decoded frames   : not reported by this browser`);
    }
    if (diag.path.indexOf("WebGL") === 0) {
      L.push(`texture uploads  : ${texUploads - diag.uploads0}  (${((texUploads - diag.uploads0) / Math.max(secs, 0.001)).toFixed(1)}/s)`);
      L.push(`framebuffer      : ${diag.fbW}x${diag.fbH}  scale ${diag.scale.toFixed(2)}`);
      L.push(`fixed foveation  : ${diag.foveation === null ? "unavailable" : diag.foveation}`);
      L.push(`rVFC throttling  : ${video.requestVideoFrameCallback ? "on" : "off (no requestVideoFrameCallback)"}`);
    }
    if (diag.vfCount) {
      const tgt = srcFps ? 1000 / srcFps : 0;
      const off = tgt ? diag.vfWall.filter((d) => Math.abs(d - tgt) > tgt * 0.25).length : 0;
      L.push(`video frames     : ${diag.vfCount} presented`);
      L.push(`  wall gap ms    : p50 ${fmtPct(diag.vfWall, 0.5)}  p95 ${fmtPct(diag.vfWall, 0.95)}  p99 ${fmtPct(diag.vfWall, 0.99)}` +
        (tgt ? `  (target ${tgt.toFixed(1)})` : ""));
      L.push(`  media gap ms   : p50 ${fmtPct(diag.vfMedia, 0.5)}  p95 ${fmtPct(diag.vfMedia, 0.95)}  p99 ${fmtPct(diag.vfMedia, 0.99)}`);
      L.push(`  uneven gaps    : ${off} of ${diag.vfWall.length} more than 25% off target`);
      L.push(`  timed by       : ${diag.vfClock || "?"}`);
      if (medMs > 0 && diag.vfWall.length) {
        const hist = {};
        for (const g of diag.vfWall) {
          const n = Math.max(0, Math.round(g / medMs));
          hist[n] = (hist[n] || 0) + 1;
        }
        const want = srcFps ? Math.round((1000 / srcFps) / medMs) : 0;
        const parts = Object.keys(hist).sort((a, b) => a - b).map((n) => {
          const pctv = ((hist[n] / diag.vfWall.length) * 100).toFixed(1);
          return `${n}x:${pctv}%${Number(n) === want ? "*" : ""}`;
        });
        L.push(`  held for       : ${parts.join("  ")}   (* = the even ${want}x cadence)`);
      }
    } else {
      L.push(`video frames     : requestVideoFrameCallback never fired`);
    }
    if (diag.buf.length) {
      const mn = Math.min.apply(null, diag.buf);
      L.push(`buffer ahead s   : min ${mn.toFixed(1)}  p10 ${fmtPct(diag.buf, 0.1)}  p50 ${fmtPct(diag.buf, 0.5)}  max ${Math.max.apply(null, diag.buf).toFixed(1)}`);
    }
    L.push(`stalls           : waiting ${diag.waiting}, stalled ${diag.stalled}` +
      (diag.bufAtWait.length ? `  (buffer at stall: ${diag.bufAtWait.map((x) => x.toFixed(1)).join(", ")}s)` : ""));
    L.push(`xr visibility    : ${diag.vis.join(" -> ") || "visible (no changes)"}`);
    L.push(`doc visibility   : ${diag.docVis.join(" -> ") || "no changes"}`);
    for (const n of diag.notes) L.push(`note             : ${n}`);
    if (srcFps && !diag.targetRate && Math.abs(xrFps / srcFps - Math.round(xrFps / srcFps)) > 0.02) {
      L.push("");
      L.push(`The headset is refusing to leave ${Math.round(xrFps)} Hz, which does not divide`);
      L.push(`evenly by ${srcFps} fps. Battery Saver caps Quest at 72 Hz — check`);
      L.push("Settings > General > Power and turn it off, then try again.");
    }
    return L.join("\n");
  };

  const reportBox = el("pre", { class: "vr-report-pre" });
  const reportPanel = el("div", { class: "vr-report", style: "display:none" });
  const reportCopy = el("button", { class: "btn" }, "Copy");
  reportCopy.addEventListener("click", () => {
    navigator.clipboard.writeText(reportBox.textContent).then(
      () => toast("Report copied"), () => toast("Could not copy"));
  });
  const reportHide = el("button", { class: "btn" }, "Hide");
  reportHide.addEventListener("click", () => { reportPanel.style.display = "none"; });
  reportPanel.appendChild(el("div", { class: "vr-report-head" }, [
    el("strong", {}, "Last VR session"), reportCopy, reportHide,
  ]));
  reportPanel.appendChild(reportBox);

  // The panel was scaffolding for a specific hunt. Now that both paths sit at
  // the right cadence, only surface it unprompted when a session actually went
  // wrong — otherwise leave it behind a button.
  const finishDiag = () => {
    if (!diag) return;
    const q1 = vq();
    const dropped = diag.quality0 && q1
      ? q1.droppedVideoFrames - diag.quality0.droppedVideoFrames : 0;
    const medMs = diag.frameTimes.length
      ? diag.frameTimes.slice().sort((a, b) => a - b)[Math.floor(diag.frameTimes.length / 2)] : 0;
    const cad = medMs > 0 && srcFps ? (1000 / medMs) / srcFps : 0;
    const flagged = diag.waiting > 0 || dropped > 2 ||
      (cad > 0 && Math.abs(cad - Math.round(cad)) > 0.02) ||
      diag.notes.some((n) => /lost|refused|never settled|could not|did not take/.test(n));
    const txt = buildReport();
    reportBox.textContent = txt;
    reportPanel.style.display = flagged ? "" : "none";
    reportBtn.style.display = "";
    console.info("[vr] session report\n" + txt);
    diag = null;
  };

  // Armed on BOTH renderer paths. On the WebGL path it throttles uploads; on
  // the compositor path it costs nothing but records how evenly the media
  // pipeline actually hands over frames — which is the one thing none of the
  // previous reports measured.
  const armRVFC = () => {
    if (!video.requestVideoFrameCallback) return;
    rvfcActive = true;
    rvfcId = video.requestVideoFrameCallback((now, meta) => {
      xrNewFrame = true;
      if (diag) {
        diag.vfCount++;
        let pt = now, clk = "callback";
        if (meta && typeof meta.presentationTime === "number") { pt = meta.presentationTime; clk = "presentationTime"; }
        else if (meta && typeof meta.expectedDisplayTime === "number") { pt = meta.expectedDisplayTime; clk = "expectedDisplayTime"; }
        diag.vfClock = clk;
        if (diag.lastVfNow) {
          const wall = pt - diag.lastVfNow;
          if (diag.vfWall.length < 20000) diag.vfWall.push(wall);
        }
        diag.lastVfNow = pt;
        if (diag.lastVfMedia !== null && meta && typeof meta.mediaTime === "number") {
          const dm = (meta.mediaTime - diag.lastVfMedia) * 1000;
          if (diag.vfMedia.length < 20000) diag.vfMedia.push(dm);
        }
        if (meta && typeof meta.mediaTime === "number") diag.lastVfMedia = meta.mediaTime;
      }
      if (xrSession) armRVFC();
      else rvfcActive = false;
    });
  };
  const stopRVFC = () => {
    if (rvfcId && video.cancelVideoFrameCallback) {
      try { video.cancelVideoFrameCallback(rvfcId); } catch (e) {}
    }
    rvfcId = 0; rvfcActive = false; xrNewFrame = true;
  };

  // A full-screen video quad needs no MSAA, no depth and no stencil, and the
  // default antialiased framebuffer costs a lot of bandwidth for nothing.
  // Fixed foveation cuts fragment cost at the edges, where the lenses blur the
  // image anyway — both matter a great deal for this shader, which does an
  // atan/asin plus a dependent fetch from a very large texture per pixel.
  const makeWebGLLayer = (session) => {
    // 1.0 is the browser's *recommended* size, which on a Quest is already
    // below native — so this must be a fraction of 1, not of the native
    // factor (that number is usually >1 and would raise the resolution).
    const native = XRWebGLLayer.getNativeFramebufferScaleFactor
      ? XRWebGLLayer.getNativeFramebufferScaleFactor(session) : 1;
    const scale = Math.min(XR_FB_SCALE, native);
    const layer = new XRWebGLLayer(session, gl, {
      antialias: false, depth: false, stencil: false, alpha: false,
      framebufferScaleFactor: scale,
    });
    let fov = null;
    try { layer.fixedFoveation = 1; fov = layer.fixedFoveation; } catch (e) {}
    if (diag) {
      diag.scale = scale;
      diag.fbW = layer.framebufferWidth; diag.fbH = layer.framebufferHeight;
      diag.foveation = fov;
    }
    return layer;
  };

  // Equirect media layer for 360°/180° (fisheye has no layer type — WebGL).
  const tryMediaLayer = (session) => {
    if (rendererPref === "webgl") { xrPathInfo = "WebGL renderer selected"; return false; }
    if (projection === "fisheye") { xrPathInfo = "fisheye needs the WebGL renderer"; return false; }
    if (typeof XRMediaBinding === "undefined") { xrPathInfo = "media layers not supported by this browser"; return false; }
    if (video.readyState < 2) {
      xrPathInfo = "video had no decoded frame when the layer was created";
      return false;
    }
    try {
      const binding = new XRMediaBinding(session);
      xrMediaLayer = binding.createEquirectLayer(video, {
        space: xrRefSpace,
        layout: layout === "sbs" ? "stereo-left-right"
          : layout === "tb" ? "stereo-top-bottom" : "mono",
        centralHorizontalAngle: projection === "eq360" ? 2 * Math.PI : Math.PI,
        upperVerticalAngle: Math.PI / 2,
        lowerVerticalAngle: -Math.PI / 2,
      });
      session.updateRenderState({ layers: [xrMediaLayer] });
      xrPathInfo = "compositor media layer";
      return true;
    } catch (e) {
      xrPathInfo = "media layer failed: " + (e && e.message ? e.message : e);
      console.warn("[vr] " + xrPathInfo, e);
      xrMediaLayer = null;
      return false;
    }
  };


  // ── XR controller input (xr-standard gamepad mapping, works on either
  // hand): stick left/right = skip ±10s (one flick, one skip), stick
  // up/down held = volume, B/Y = stop playback and leave VR, trigger =
  // play/pause. Haptic pulses acknowledge actions since there's no HUD. ──
  let xrInputState = {};
  let xrLastT = 0;

  const xrPulse = (gp, strength, ms) => {
    try {
      const h = gp.hapticActuators && gp.hapticActuators[0];
      if (h && h.pulse) h.pulse(strength, ms);
    } catch (e) {}
  };

  const handleXRInput = (session, dt) => {
    for (const src of session.inputSources) {
      const gp = src.gamepad;
      if (!gp) continue;
      const hand = src.handedness || "none";
      const st = xrInputState[hand] || (xrInputState[hand] = { x: 0, b: false, trig: false, zoomWarned: false });
      // xr-standard puts the thumbstick on axes[2]/[3]; fall back to [0]/[1].
      const ax = gp.axes.length > 3 ? gp.axes[2] : (gp.axes[0] || 0);
      const ay = gp.axes.length > 3 ? gp.axes[3] : (gp.axes[1] || 0);

      // Horizontal flick: seek. Edge-triggered with hysteresis — fires once
      // per push, re-arms when the stick returns near centre.
      if (Math.abs(ax) > 0.6 && st.x === 0) {
        st.x = Math.sign(ax);
        if (video.duration) {
          const t = video.currentTime + (ax > 0 ? 10 : -10);
          video.currentTime = Math.max(0, Math.min(video.duration - 0.1, t));
        }
        xrPulse(gp, 0.4, 50);
      } else if (Math.abs(ax) < 0.3) {
        st.x = 0;
      }

      // Vertical hold: zoom. Stick forward is negative y, so invert — up
      // zooms in, down zooms out (back to 1x). Full deflection roughly
      // doubles the zoom per second. On a media-layer session a deliberate
      // push first switches rendering to WebGL, where the shader can zoom.
      if (Math.abs(ay) > 0.25) {
        if (xrMediaLayer) {
          // Zoom needs the shader. Swapping the compositor layer out for a
          // WebGL one mid-session tears down the layer the compositor is
          // presenting and kills playback, so the renderer is now chosen up
          // front instead. Buzz twice to say "not in this mode".
          if (Math.abs(ay) > 0.5 && !st.zoomWarned) {
            st.zoomWarned = true;
            xrPulse(gp, 0.2, 40);
            setTimeout(() => xrPulse(gp, 0.2, 40), 120);
            if (diag) diag.notes.push("zoom pressed — unavailable on the compositor renderer");
          }
        } else {
          const prev = xrZoom;
          xrZoom = Math.max(1, Math.min(3, xrZoom * (1 - ay * dt)));
          if ((xrZoom === 1 || xrZoom === 3) && prev !== xrZoom) {
            xrPulse(gp, 0.25, 30); // bump at the ends of the range
          }
        }
      }

      // B / Y (xr-standard button 5): stop playback and leave VR.
      const bPressed = !!(gp.buttons[5] && gp.buttons[5].pressed);
      if (bPressed && !st.b) {
        xrPulse(gp, 0.6, 80);
        video.pause();
        session.end().catch(() => {});
      }
      st.b = bPressed;

      // Trigger (button 0): play/pause without leaving the session.
      const trig = !!(gp.buttons[0] && gp.buttons[0].pressed);
      if (trig && !st.trig) {
        if (video.paused) video.play().catch(() => {}); else video.pause();
        xrPulse(gp, 0.3, 40);
      }
      st.trig = trig;
    }
  };

  // Generic column-major 4x4 inverse (gl-matrix style) for the eye
  // projection matrices, which are asymmetric on most headsets.
  const invert4 = (m) => {
    const a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3],
      a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7],
      a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11],
      a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15];
    const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10,
      b02 = a00 * a13 - a03 * a10, b03 = a01 * a12 - a02 * a11,
      b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12,
      b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30,
      b08 = a20 * a33 - a23 * a30, b09 = a21 * a32 - a22 * a31,
      b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
    let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    if (!det) return null;
    det = 1.0 / det;
    return new Float32Array([
      (a11 * b11 - a12 * b10 + a13 * b09) * det,
      (a02 * b10 - a01 * b11 - a03 * b09) * det,
      (a31 * b05 - a32 * b04 + a33 * b03) * det,
      (a22 * b04 - a21 * b05 - a23 * b03) * det,
      (a12 * b08 - a10 * b11 - a13 * b07) * det,
      (a00 * b11 - a02 * b08 + a03 * b07) * det,
      (a32 * b02 - a30 * b05 - a33 * b01) * det,
      (a20 * b05 - a22 * b02 + a23 * b01) * det,
      (a10 * b10 - a11 * b08 + a13 * b06) * det,
      (a01 * b08 - a00 * b10 - a03 * b06) * det,
      (a30 * b04 - a31 * b02 + a33 * b00) * det,
      (a21 * b02 - a20 * b04 - a23 * b00) * det,
      (a11 * b07 - a10 * b09 - a12 * b06) * det,
      (a00 * b09 - a01 * b07 + a02 * b06) * det,
      (a31 * b01 - a30 * b03 - a32 * b00) * det,
      (a20 * b03 - a21 * b01 + a22 * b00) * det,
    ]);
  };

  const xrFrame = (t, frame) => {
    const session = xrSession;
    if (!session) return;
    session.requestAnimationFrame(xrFrame);
    const dt = xrLastT ? Math.min((t - xrLastT) / 1000, 0.1) : 0;
    if (diag) {
      diag.frames++;
      if (!diag.firstFrameT) diag.firstFrameT = t;
      if (diag.lastFrameT) {
        const ms = t - diag.lastFrameT;
        if (diag.frameTimes.length < 20000) diag.frameTimes.push(ms);
        if (ms > 25) diag.longFrames++;
      }
      diag.lastFrameT = t;
    }
    recentT.push(t);
    if (recentT.length > 90) recentT.shift();
    if (diag && t - diag.lastBufT > 1000) { diag.lastBufT = t; diag.buf.push(bufAhead()); }
    pumpFrameRate(session, t);
    xrLastT = t;
    handleXRInput(session, dt);
    if (!xrSession) return; // B button may have ended the session just now
    if (xrMediaLayer) return; // compositor renders the video — input only
    if (glLost) {
      // Nothing can be drawn until the context comes back. Give it a moment,
      // then end the session rather than leaving a frozen headset behind.
      if (!glLostAt) glLostAt = t;
      else if (t - glLostAt > 4000) {
        glLostAt = 0;
        toast("VR renderer lost its graphics context — exiting VR");
        session.end().catch(() => {});
      }
      return;
    }
    glLostAt = 0;
    const pose = frame.getViewerPose(xrRefSpace);
    if (!pose) return;
    const layer = session.renderState.baseLayer;
    if (!layer) return; // mid-switch between renderers
    gl.bindFramebuffer(gl.FRAMEBUFFER, layer.framebuffer);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (xrNewFrame || !rvfcActive) {
      uploadFrame();
      xrNewFrame = false;
    }
    gl.uniform1i(uUseXR, 1);
    gl.uniform1f(uZoom, xrZoom);
    gl.uniform1i(uMode, modeOf());
    const sc = scaleOf();
    for (const view of pose.views) {
      const vp = layer.getViewport(view);
      if (!vp || !vp.width) continue;
      gl.viewport(vp.x, vp.y, vp.width, vp.height);
      const invProj = invert4(view.projectionMatrix);
      if (invProj) gl.uniformMatrix4fv(uInvProj, false, invProj);
      // view.transform.matrix maps eye space -> reference space; strip the
      // translation so the projection sphere stays centred on the viewer.
      const m = new Float32Array(view.transform.matrix);
      m[12] = 0; m[13] = 0; m[14] = 0;
      gl.uniformMatrix4fv(uInvView, false, m);
      // Per-eye texture half for stereo layouts (left eye also covers "none").
      const right = view.eye === "right";
      let off = [0, 0];
      if (layout === "sbs") off = [right ? 0.5 : 0, 0];
      else if (layout === "tb") off = [0, right ? 0.5 : 0];
      gl.uniform2f(uUvScale, sc[0], sc[1]);
      gl.uniform2f(uUvOffset, off[0], off[1]);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }
  };

  // Refresh-rate matching. Two lessons from the last run: the promise from
  // updateTargetFrameRate can never settle (the spec's own discussion warns
  // the request may simply not be respected), and Meta's docs call it from
  // inside the XR frame callback rather than outside the loop. So: drive it
  // from the frame loop, confirm via session.frameRate / measured intervals
  // rather than the promise, and fall through to the next-best rate if one
  // doesn't take.
  let rateCandidates = [], rateIdx = 0, rateLastT = 0, rateDone = false;
  let recentT = [];

  const measuredHz = () => {
    if (recentT.length < 20) return 0;
    const d = [];
    for (let i = 1; i < recentT.length; i++) d.push(recentT[i] - recentT[i - 1]);
    d.sort((a, b) => a - b);
    const med = d[Math.floor(d.length / 2)];
    return med > 0 ? 1000 / med : 0;
  };

  const initFrameRatePlan = (session) => {
    rateCandidates = []; rateIdx = 0; rateLastT = 0; rateDone = false; recentT = [];
    let rates = null;
    try { rates = session.supportedFrameRates; } catch (e) {}
    if (diag) diag.rates = rates && rates.length ? Array.from(rates).join(", ") : "not reported";
    if (!srcFps) {
      rateDone = true;
      if (diag) diag.notes.push("no frame rate in metadata — cadence not matched");
      return;
    }
    if (!rates || !rates.length || !session.updateTargetFrameRate) { rateDone = true; return; }
    // The WebGL path keeps a 90 Hz ceiling for shader headroom; the compositor
    // path has no per-frame work of ours and can take whatever divides best.
    const max = xrMediaLayer ? Infinity : 90;
    rateCandidates = Array.from(rates)
      .filter((r) => r <= max)
      .map((r) => {
        const n = Math.max(1, Math.round(r / srcFps));
        return { r, err: Math.abs(r / srcFps - n) / n };
      })
      .sort((a, b) => a.err - b.err || Math.abs(a.r - 90) - Math.abs(b.r - 90))
      .map((o) => o.r)
      .slice(0, 3);
    if (diag) diag.ratePlan = rateCandidates.join(" then ");
  };

  const pumpFrameRate = (session, t) => {
    if (rateDone) return;
    const cur = session.frameRate || measuredHz();
    if (diag && cur) diag.gotRate = cur;
    const want = rateCandidates[rateIdx];
    if (want === undefined) {
      rateDone = true;
      if (diag) diag.notes.push("no offered rate could be applied — cadence left uneven");
      return;
    }
    if (cur && Math.abs(cur - want) < 1) {
      rateDone = true;
      if (diag) diag.targetRate = want;
      return;
    }
    if (!rateLastT) {
      rateLastT = t;
      try {
        const p = session.updateTargetFrameRate(want);
        if (p && p.then) p.then(() => {}, () => {}); // never awaited, never trusted
        if (diag) diag.notes.push(`requested ${want} Hz from inside the frame loop`);
      } catch (e) {
        if (diag) diag.notes.push(`updateTargetFrameRate(${want}) threw: ${e && e.message ? e.message : e}`);
      }
      return;
    }
    // Give each candidate a few seconds to take effect before moving on.
    if (t - rateLastT > 3000) {
      if (diag) diag.notes.push(`${want} Hz did not take`);
      rateIdx++; rateLastT = 0;
    }
  };

  const enterVR = async () => {
    if (xrSession) { xrSession.end().catch(() => {}); return; }
    let session;
    try {
      // requestSession first — it must run inside the user gesture.
      // Don't ask for "layers" when the shader renderer is forced: with the
      // feature enabled the compositor owns presentation, and mixing that with
      // a baseLayer is the kind of ambiguity that ends in a black headset.
      const feats = rendererPref === "webgl" ? ["local-floor"] : ["local-floor", "layers"];
      session = await navigator.xr.requestSession("immersive-vr", { optionalFeatures: feats });
    } catch (e) {
      toast("Could not start VR session" + (e && e.message ? `: ${e.message}` : ""));
      return;
    }
    diag = state.vrDebug ? newDiag() : null;
    try {
      try { xrRefSpace = await session.requestReferenceSpace("local"); }
      catch (e) { xrRefSpace = await session.requestReferenceSpace("viewer"); }

      // Start playback *before* building the layer. A media layer needs the
      // element to have decoded data — creating one against a video that is
      // still at HAVE_NOTHING throws, and the session silently drops to the
      // slow WebGL path.
      video.setAttribute("style", VIDEO_STYLE_XR);
      // Bounded: a play() promise that never settles would strand the session
      // before it ever submits a frame.
      try {
        await Promise.race([
          video.play().catch(() => {}),
          new Promise((res) => setTimeout(res, 2000)),
        ]);
      } catch (e) {}
      if (video.readyState < 2) {
        await new Promise((res) => {
          const done = () => { clearTimeout(tm); video.removeEventListener("loadeddata", done); res(); };
          const tm = setTimeout(done, 3000);
          video.addEventListener("loadeddata", done, { once: true });
        });
        if (video.readyState < 2 && diag) diag.notes.push("no decoded frame after 3s wait");
      }

      // Prefer a compositor media layer (smooth, no WebGL copies); fall back
      // to WebGL rendering when unsupported or when projection is fisheye.
      const usingWebGL = !tryMediaLayer(session);
      if (usingWebGL) {
        await gl.makeXRCompatible();
        session.updateRenderState({ baseLayer: makeWebGLLayer(session) });
      }
      if (usingWebGL || state.vrDebug) armRVFC();
    } catch (e) {
      video.setAttribute("style", VIDEO_STYLE_2D);
      diag = null;
      session.end().catch(() => {});
      toast("VR setup failed" + (e && e.message ? `: ${e.message}` : ""));
      return;
    }
    xrSession = session;
    xrInputState = {};
    xrLastT = 0;
    xrZoom = 1;
    if (diag) {
      diag.path = xrMediaLayer ? "compositor media layer" : `WebGL (${xrPathInfo})`;
      diag.quality0 = vq();
      diag.playT0 = Date.now();
      diag.uploads0 = texUploads;
    }
    video.addEventListener("waiting", onWaiting);
    video.addEventListener("stalled", onStalled);
    document.addEventListener("visibilitychange", onDocVis);
    const onXRVis = () => { if (diag) diag.vis.push(session.visibilityState); };
    session.addEventListener("visibilitychange", onXRVis);
    console.info("[vr] renderer: " + xrPathInfo);
    toast(xrMediaLayer ? "VR: smooth compositor playback"
      : `VR: WebGL renderer (${xrPathInfo})`);
    cancelAnimationFrame(raf); // pause the magic-window loop while immersed
    session.addEventListener("end", () => {
      xrSession = null; xrRefSpace = null;
      if (xrMediaLayer) { try { xrMediaLayer.destroy(); } catch (e) {} xrMediaLayer = null; }
      stopRVFC();
      finishDiag();
      video.removeEventListener("waiting", onWaiting);
      video.removeEventListener("stalled", onStalled);
      document.removeEventListener("visibilitychange", onDocVis);
      // Only re-park the element if this viewer is still mounted — the session
      // can end because the user switched to the flat player, which has
      // already cleared the style and needs the video visible.
      if (canvas.isConnected) video.setAttribute("style", VIDEO_STYLE_2D);
      if (xrBtn) xrBtn.textContent = "Enter VR";
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      if (canvas.isConnected) raf = requestAnimationFrame(draw);
    });
    if (xrBtn) xrBtn.textContent = "Exit VR";
    initFrameRatePlan(session);
    session.addEventListener("frameratechange", () => {
      if (diag) diag.notes.push(`frameratechange -> ${session.frameRate}`);
    });
    session.requestAnimationFrame(xrFrame);
  };

  const hint = el("div", { class: "vr-hint" }, "Drag to look · scroll to zoom");

  if (navigator.xr && navigator.xr.isSessionSupported) {
    navigator.xr.isSessionSupported("immersive-vr").then((ok) => {
      if (!ok || xrBtn) return;
      xrBtn = el("button", { class: "vr-xr", title: "Play in your headset" }, "Enter VR");
      xrBtn.addEventListener("click", enterVR);
      bar.insertBefore(xrBtn, fsBtn);
      hint.textContent = "Drag to look · scroll to zoom · Enter VR for your headset";
    }).catch(() => {});
  }

  stage.appendChild(hint);
  stage.appendChild(bar);
  setTimeout(() => { hint.style.opacity = "0"; }, 3500);

  let hideTimer;
  const showBar = () => {
    bar.classList.remove("hidden");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => bar.classList.add("hidden"), 3000);
  };
  stage.addEventListener("pointermove", showBar);
  showBar();

  host.appendChild(stage);
  host.appendChild(reportPanel);
  syncPlay();
  raf = requestAnimationFrame(draw);
  if (state.autoplay) video.play().catch(() => {});

  return () => {
    cancelAnimationFrame(raf);
    clearTimeout(hideTimer);
    if (xrSession) xrSession.end().catch(() => {});
    if (document.fullscreenElement === stage) document.exitFullscreen().catch(() => {});
  };
}

async function renderVideo(id) {
  state.view = "video";
  const c = main();
  c.innerHTML = `<div class="empty"><div class="spinner" style="margin:0 auto"></div></div>`;
  let v;
  try {
    v = await api.get(`/api/video/${id}`);
  } catch (e) {
    c.innerHTML = `<div class="empty"><h2>Video not found</h2></div>`;
    return;
  }

  c.innerHTML = "";
  const detail = el("div", { class: "detail" });

  const resumeAt = v.position && v.position > 0 ? v.position : 0;
  const vProps = { controls: "", src: `/api/stream/${id}`, preload: "metadata" };
  // Don't autoplay when there's a resume point — let the user choose.
  if (state.autoplay && !resumeAt) vProps.autoplay = "";
  if (v.has_thumb) vProps.poster = `/api/thumb/${id}`;
  const video = el("video", vProps);

  // A browser that can't decode the video codec doesn't error — it plays the
  // audio track and reports videoWidth 0, which looks exactly like an audio
  // file. HEVC is the usual culprit: Chrome needs a hardware HEVC decoder on
  // Linux/Windows, and Firefox only got it on Linux in 137. The indexer knows
  // from ffprobe that a video stream exists, so a mismatch is conclusive.
  let codecWarned = false;
  const checkVideoTrack = () => {
    if (codecWarned || !v.width || !v.height) return;   // no video stream indexed
    if (video.readyState < 1 || video.videoWidth > 0) return;
    codecWarned = true;
    const codec = (v.vcodec || "this video codec").toUpperCase();
    const note = el("div", { class: "codec-warning" }, [
      el("strong", {}, `No picture: your browser can't decode ${codec}`),
      el("p", {}, `The file has a ${v.width}\u00d7${v.height} ${codec} video track, but this ` +
        "browser decoded only the audio. The file isn't damaged — it will play in " +
        "VLC or mpv, and often in a different browser."),
      el("p", {}, codec === "HEVC" || codec === "H265"
        ? "HEVC needs hardware decoding support in the browser: Chrome requires a " +
          "hardware HEVC decoder on Linux and Windows, Firefox added Linux support " +
          "in version 137, and Safari handles it natively. Re-encoding to H.264 " +
          "makes it play everywhere."
        : "Re-encoding to H.264 in an MP4 container makes it play everywhere."),
      el("code", { class: "codec-cmd" },
        `ffmpeg -i "${v.rel_path || v.filename || "input"}" -c:v libx264 -crf 20 -c:a aac output.mp4`),
    ]);
    video.parentNode && video.parentNode.insertBefore(note, video.nextSibling);
  };
  video.addEventListener("loadeddata", checkVideoTrack);
  video.addEventListener("playing", checkVideoTrack);

  // Record a play the first time playback actually starts — not merely when the
  // info page is opened — so browsing to a video doesn't mark it watched.
  let playRecorded = false;
  video.addEventListener("play", () => {
    if (playRecorded) return;
    playRecorded = true;
    api.post(`/api/video/${id}/played`).catch(() => {});
  });

  // Seek helper that waits for metadata if needed.
  const seekAndPlay = (t) => {
    const doSeek = () => { try { video.currentTime = t; } catch (e) {} video.play().catch(() => {}); };
    if (video.readyState >= 1) doSeek();
    else video.addEventListener("loadedmetadata", doSeek, { once: true });
  };

  // ── Progress / resume / watched tracking ──
  const reportProgress = () => {
    if (!video.duration || isNaN(video.duration)) return;
    api.post(`/api/video/${id}/progress`, {
      position: video.currentTime || 0, duration: video.duration,
    }).catch(() => {});
  };
  let lastSave = 0;
  video.addEventListener("timeupdate", () => {
    const now = Date.now();
    if (now - lastSave > 5000) { lastSave = now; reportProgress(); }
  });
  video.addEventListener("pause", reportProgress);
  video.addEventListener("ended", reportProgress);
  // Flush on tab close via a beacon (survives page unload).
  const beacon = () => {
    if (!video.duration || isNaN(video.duration)) return;
    try {
      const blob = new Blob(
        [JSON.stringify({ position: video.currentTime || 0, duration: video.duration })],
        { type: "application/json" }
      );
      navigator.sendBeacon(`/api/video/${id}/progress`, blob);
    } catch (e) {}
  };
  window.addEventListener("pagehide", beacon);
  // Called by the router when navigating away from this page.
  leaveVideo = () => {
    window.removeEventListener("pagehide", beacon);
    clearTimeout(aiPollTimer); // stop AI status polling for this page
    reportProgress();
  };

  // ── Player host: flat player or VR viewer ──
  let vrMode = v.vr;
  let vrCleanup = null;
  const playerHost = el("div", {});
  const viewerToolbar = el("div", { class: "sortbar", style: "margin-bottom:12px;display:none" });

  const renderViewerToolbar = () => {
    viewerToolbar.innerHTML = "";
    viewerToolbar.style.display = v.vr ? "flex" : "none";
    if (!v.vr) return;
    viewerToolbar.appendChild(el("span", { class: "sb-label" }, "View"));
    viewerToolbar.appendChild(el("button", { class: "btn" + (vrMode ? " primary" : ""), onClick: () => { if (!vrMode) { vrMode = true; mountPlayer(); } } }, [el("span", { html: ICON.vr }), "VR"]));
    viewerToolbar.appendChild(el("button", { class: "btn" + (!vrMode ? " primary" : ""), onClick: () => { if (vrMode) { vrMode = false; mountPlayer(); } } }, "Flat"));
  };

  const mountPlayer = () => {
    if (vrCleanup) { vrCleanup(); vrCleanup = null; }
    playerHost.innerHTML = "";
    if (vrMode) {
      // A throw in here used to escape into renderVideo and leave the page
      // half-built with no working navigation. Fall back to the flat player
      // instead, so a viewer bug costs the VR view and nothing else.
      try {
        vrCleanup = mountVRViewer(playerHost, video, v, id);
      } catch (e) {
        console.error("[vr] viewer failed to mount", e);
        vrCleanup = null;
        vrMode = false;
        playerHost.innerHTML = "";
        video.removeAttribute("style");
        video.setAttribute("controls", "");
        playerHost.appendChild(el("div", { class: "player-shell" }, [video]));
        toast("VR viewer failed to start — showing the flat player");
      }
    } else {
      video.removeAttribute("style");
      video.setAttribute("controls", "");
      playerHost.appendChild(el("div", { class: "player-shell" }, [video]));
    }
    renderViewerToolbar();
  };

  detail.appendChild(viewerToolbar);
  detail.appendChild(playerHost);
  mountPlayer();

  // ── Resume bar ──
  if (resumeAt) {
    const bar = el("div", { class: "resume-bar" }, [
      el("span", { class: "rb-text" }, `You stopped at ${fmtDuration(resumeAt)}`),
      el("div", { class: "resume-actions" }, [
        el("button", { class: "btn primary", onClick: () => { seekAndPlay(resumeAt); bar.remove(); } },
          [el("span", { html: ICON.play }), "Resume"]),
        el("button", { class: "btn", onClick: () => { seekAndPlay(0); bar.remove(); } },
          "Play from beginning"),
      ]),
    ]);
    detail.appendChild(bar);
  }

  detail.appendChild(el("h1", {}, v.title));
  detail.appendChild(el("div", { class: "path" }, v.rel_path));

  // ── AI summary (between the video and the specifications) ──
  const aiPanel = el("div", { class: "panel ai-panel" });
  detail.appendChild(aiPanel);
  let aiPollTimer = null;

  const renderAIPanel = () => {
    aiPanel.innerHTML = "";
    const head = el("div", { class: "ai-head" }, [
      el("h3", {}, [el("span", { html: ICON.sparkle }), "AI Summary"]),
    ]);
    aiPanel.appendChild(head);

    if (v.ai_summary) {
      aiPanel.appendChild(el("p", { class: "ai-text" }, v.ai_summary));
      aiPanel.appendChild(el("div", { class: "ai-meta" },
        `${v.ai_model || "unknown model"} · ${fmtDate(v.ai_summary_at)}`));
    } else {
      aiPanel.appendChild(el("p", { class: "ai-text faint" },
        "No summary yet. Analyze this video to generate one from sampled frames."));
    }

    const btn = el("button", { class: "btn" + (v.ai_summary ? "" : " primary") },
      [el("span", { html: ICON.sparkle }), v.ai_summary ? "Regenerate" : "Analyze video"]);
    btn.addEventListener("click", async () => {
      try {
        const r = await api.post("/api/ai/analyze", { video_id: id, force: true });
        if (r.status === "already_running") { toast("An AI job is already running"); return; }
        showAIProgress();
      } catch (e) { toast(e.message || "Could not start analysis"); }
    });
    head.appendChild(btn);
  };

  const showAIProgress = () => {
    aiPanel.innerHTML = "";
    aiPanel.appendChild(el("div", { class: "ai-head" }, [
      el("h3", {}, [el("span", { html: ICON.sparkle }), "AI Summary"]),
    ]));
    aiPanel.appendChild(el("div", { class: "ai-running" }, [
      el("div", { class: "spinner" }),
      el("span", {}, "Analyzing video… sampling frames and querying the model."),
    ]));
    pollAI();
  };

  const pollAI = () => {
    clearTimeout(aiPollTimer);
    aiPollTimer = setTimeout(async () => {
      if (!aiPanel.isConnected) return; // navigated away
      let st;
      try { st = await api.get("/api/ai/status"); } catch (e) { return pollAI(); }
      const stillOnThis = st.running && st.current_video_id === id;
      if (stillOnThis) return pollAI();
      // Our video is done (or the job ended) — refresh the summary.
      try {
        const fresh = await api.get(`/api/video/${id}`);
        v.ai_summary = fresh.ai_summary;
        v.ai_summary_at = fresh.ai_summary_at;
        v.ai_model = fresh.ai_model;
      } catch (e) {}
      if (!v.ai_summary && st.last_error) toast(`AI analysis failed: ${st.last_error}`);
      renderAIPanel();
    }, 2500);
  };

  // If a batch job is currently analyzing this exact video, show progress.
  try {
    const st = await api.get("/api/ai/status");
    if (st.running && st.current_video_id === id) showAIProgress();
    else renderAIPanel();
  } catch (e) { renderAIPanel(); }

  const cols = el("div", { class: "detail-cols" });

  // left: metadata
  const left = el("div", {});
  const rows = [
    ["Resolution", v.width && v.height ? `${v.width} × ${v.height}${resLabel(v.width, v.height) ? "  (" + resLabel(v.width, v.height) + ")" : ""}` : "—"],
    ["Duration", fmtDuration(v.duration)],
    ["Frame rate", v.fps ? `${v.fps} fps` : "—"],
    ["Video codec", v.vcodec || "—"],
    ["Audio codec", v.acodec || "—"],
    ["Bitrate", v.bitrate ? `${(v.bitrate / 1e6).toFixed(2)} Mbps` : "—"],
    ["Container", v.container || "—"],
    ["File size", fmtBytes(v.size)],
    ["Last played", fmtDate(v.last_played)],
    ["Play count", String(v.play_count)],
    ["Status", v.watched ? "Watched" : (v.position > 0 ? `In progress (${fmtDuration(v.position)})` : "Not watched")],
  ];
  const table = el("table", { class: "meta-table" });
  rows.forEach(([k, val]) => {
    table.appendChild(el("tr", {}, [el("td", {}, k), el("td", {}, val)]));
  });
  left.appendChild(table);

  // history
  if (v.history && v.history.length) {
    left.appendChild(el("h3", { class: "section-title", style: "margin-top:24px" }, "Recent plays"));
    const ul = el("ul", { class: "hist-list" });
    v.history.forEach((ts) => ul.appendChild(el("li", {}, fmtDate(ts))));
    left.appendChild(ul);
  }
  cols.appendChild(left);

  // right column
  const right = el("div", {});

  // ── Favorite toggle ──
  const favLabel = el("span", {}, v.favorite ? "Favorited" : "Favorite");
  const favBtn = el("button", {
    class: "btn fav" + (v.favorite ? " on" : ""),
    style: "width:100%;justify-content:center;margin-bottom:14px",
  }, [el("span", { html: ICON.heart }), favLabel]);
  favBtn.addEventListener("click", async () => {
    const newVal = !v.favorite;
    try {
      await api.post(`/api/video/${id}/favorite`, { value: newVal });
      v.favorite = newVal;
      favBtn.classList.toggle("on", newVal);
      favLabel.textContent = newVal ? "Favorited" : "Favorite";
      toast(newVal ? "Added to favorites" : "Removed from favorites");
    } catch (e) { toast("Could not update favorite"); }
  });
  right.appendChild(favBtn);

  // ── Tags: current tags + autocomplete input + popular tag chips ──
  let allTags = [];
  try { allTags = (await api.get("/api/tags")).tags; } catch (e) {}
  let currentTags = v.tags || [];

  const tagPanel = el("div", { class: "panel" });
  tagPanel.appendChild(el("h3", {}, "Tags"));
  const tagList = el("div", { class: "tags" });
  const datalist = el("datalist", { id: "tag-suggest" });
  const popWrap = el("div", { class: "tag-pick", style: "margin-top:14px" });

  const renderCurrent = () => {
    tagList.innerHTML = "";
    if (!currentTags.length) {
      tagList.appendChild(el("span", { style: "color:var(--text-faint);font-size:13px" }, "No tags yet"));
    }
    currentTags.forEach((t) => {
      tagList.appendChild(el("span", { class: "tag" }, [
        t,
        el("span", {
          class: "x", title: "Remove",
          onClick: async () => {
            const r = await api.del(`/api/video/${id}/tags/${encodeURIComponent(t)}`);
            currentTags = r.tags;
            await refreshAllTags();
            renderCurrent(); renderPopular(); renderDatalist(); loadTree();
          },
        }, "×"),
      ]));
    });
  };

  const renderDatalist = () => {
    datalist.innerHTML = "";
    allTags.forEach((t) => datalist.appendChild(el("option", { value: t.name })));
  };

  const renderPopular = () => {
    popWrap.innerHTML = "";
    const applied = new Set(currentTags.map((t) => t.toLowerCase()));
    const top = [...allTags]
      .sort((a, b) => b.count - a.count)
      .filter((t) => !applied.has(t.name.toLowerCase()))
      .slice(0, 20);
    if (!top.length) { popWrap.style.display = "none"; return; }
    popWrap.style.display = "flex";
    popWrap.appendChild(el("div", {
      style: "width:100%;font-size:12px;color:var(--text-faint);margin-bottom:2px",
    }, "Popular tags — click to add"));
    top.forEach((t) => popWrap.appendChild(
      el("div", { class: "chip", onClick: () => applyTag(t.name) }, t.name)
    ));
  };

  const refreshAllTags = async () => {
    try { allTags = (await api.get("/api/tags")).tags; } catch (e) {}
  };

  const applyTag = async (name) => {
    name = (name || "").trim();
    if (!name) return;
    const r = await api.post(`/api/video/${id}/tags`, { name });
    refreshTagWarning();
    currentTags = r.tags;
    await refreshAllTags();
    renderCurrent(); renderPopular(); renderDatalist(); loadTree();
  };

  const tagInput = el("input", {
    type: "text", placeholder: "Add a tag…", list: "tag-suggest", autocomplete: "off",
  });
  const addFromInput = async () => { await applyTag(tagInput.value); tagInput.value = ""; };
  tagInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addFromInput(); } });

  renderCurrent(); renderDatalist(); renderPopular();
  tagPanel.appendChild(tagList);
  tagPanel.appendChild(el("div", { class: "tag-input" }, [
    tagInput,
    el("button", { class: "btn primary", onClick: addFromInput }, "Add"),
  ]));
  tagPanel.appendChild(datalist);
  tagPanel.appendChild(popWrap);
  right.appendChild(tagPanel);

  // channel-from-here shortcut
  const folder = v.folder || "";
  right.appendChild(el("button", {
    class: "btn", style: "width:100%;margin-top:14px;justify-content:center",
    onClick: () => startChannel(folder, []),
  }, [el("span", { html: ICON.play }), `Channel this folder`]));

  // ── VR content toggle (persistent) ──
  const vrLabel = el("span", {}, v.vr ? "VR content: on" : "Mark as VR");
  const vrBtn = el("button", {
    class: "btn" + (v.vr ? " on" : ""),
    style: "width:100%;margin-top:14px;justify-content:center",
  }, [el("span", { html: ICON.vr }), vrLabel]);
  vrBtn.addEventListener("click", async () => {
    const newVal = !v.vr;
    try {
      await api.post(`/api/video/${id}/vr`, {
        vr: newVal, projection: v.vr_projection || "eq180", layout: v.vr_layout || "mono",
      });
      v.vr = newVal;
      vrBtn.classList.toggle("on", newVal);
      vrLabel.textContent = newVal ? "VR content: on" : "Mark as VR";
      vrMode = newVal;
      mountPlayer();
      loadVRNav();
      toast(newVal ? "Marked as VR" : "Unmarked VR");
    } catch (e) { toast("Could not update"); }
  });
  right.appendChild(vrBtn);

  // ── Delete video ──
  const delBtn = el("button", {
    class: "btn ghost", style: "width:100%;margin-top:14px;justify-content:center;color:var(--danger)",
  }, [el("span", { html: ICON.trash }), "Delete video"]);
  delBtn.addEventListener("click", async () => {
    if (!confirm(`Delete this video from disk?\n\n${v.rel_path}\n\nThis cannot be undone.`)) return;
    try { await api.del(`/api/video/${id}/file`); } catch (e) { toast("Could not delete video"); return; }
    toast("Video deleted");
    loadTree(); loadVRNav();
    navigate("browse", folder);
  });
  right.appendChild(delBtn);

  cols.appendChild(right);
  detail.appendChild(cols);
  c.appendChild(detail);
}

async function renderHistory() {
  setActiveNav("history");
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "Watched")]));
  const data = await api.get("/api/history?limit=100");
  if (!data.videos.length) {
    c.appendChild(el("div", { class: "empty" }, [
      el("div", { html: ICON.play }),
      el("h2", {}, "Nothing watched yet"),
      el("p", {}, "Videos you play show up here — the last 100, most recent first."),
    ]));
    return;
  }
  c.appendChild(viewBar(renderHistory));
  c.appendChild(videoGrid(data.videos));
}

async function renderFavorites() {
  setActiveNav("favorites");
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "Favorites")]));
  const data = await api.get("/api/favorites");
  if (!data.videos.length) {
    c.appendChild(el("div", { class: "empty" }, [
      el("div", { html: ICON.heart }),
      el("h2", {}, "No favorites yet"),
      el("p", {}, "Tap the heart on any video to add it here."),
    ]));
    return;
  }
  c.appendChild(viewBar(renderFavorites));
  c.appendChild(videoGrid(data.videos));
}

async function renderNew() {
  setActiveNav("new");
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "New")]));
  c.appendChild(el("p", { style: "color:var(--text-dim);margin-top:-8px" },
    "The 100 most recently added videos."));
  const data = await api.get("/api/new?limit=100");
  if (!data.videos.length) {
    c.appendChild(el("div", { class: "empty" }, [
      el("div", { html: ICON.film }),
      el("h2", {}, "Nothing here yet"),
      el("p", {}, "Once your library is indexed, the newest videos show up here."),
    ]));
    return;
  }
  c.appendChild(viewBar(renderNew));
  c.appendChild(videoGrid(data.videos));
}

const PAGE_SIZE = 60;

function pageWindow(cur, pages) {
  const set = new Set([0, pages - 1]);
  for (let i = cur - 1; i <= cur + 1; i++) if (i >= 0 && i < pages) set.add(i);
  const sorted = [...set].sort((a, b) => a - b);
  const out = [];
  let prev = -1;
  for (const n of sorted) { if (n - prev > 1) out.push("..."); out.push(n); prev = n; }
  return out;
}

function pager(total, pageSize, current, onGo) {
  const pages = Math.ceil(total / pageSize);
  if (pages <= 1) return null;
  const wrap = el("div", { class: "pager" });
  const btn = (label, page, opts = {}) => el("button", {
    ...(opts.active ? { class: "active" } : {}),
    ...(opts.disabled ? { disabled: "" } : {}),
    onClick: () => { if (!opts.disabled && !opts.active) onGo(page); },
  }, label);
  wrap.appendChild(btn("‹ Prev", current - 1, { disabled: current <= 0 }));
  pageWindow(current, pages).forEach((n) => {
    if (n === "...") wrap.appendChild(el("span", { class: "pg-ellipsis" }, "…"));
    else wrap.appendChild(btn(String(n + 1), n, { active: n === current }));
  });
  wrap.appendChild(btn("Next ›", current + 1, { disabled: current >= pages - 1 }));
  return wrap;
}

async function renderAllVideos() {
  setActiveNav("allvideos");
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "All Videos")]));
  const page = state.page || 0;
  let data;
  try {
    data = await api.get(`/api/videos?sort=${state.sort.field}&order=${state.sort.order}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`);
  } catch (e) {
    c.appendChild(el("div", { class: "empty" }, [el("h2", {}, "Could not load videos")]));
    return;
  }
  if (!data.count) {
    c.appendChild(el("div", { class: "empty" }, [
      el("div", { html: ICON.film }),
      el("h2", {}, "No videos yet"),
      el("p", {}, "Add videos to your library folder and rescan."),
    ]));
    return;
  }
  const pages = Math.ceil(data.count / PAGE_SIZE);
  if (pages > 0 && page >= pages) return navigate("allvideos", pages, true);

  if (state.selectMode) {
    c.appendChild(selectToolbar(renderAllVideos));
  } else {
    const selectBtn = el("button", {
      class: "btn", onClick: () => { state.selectMode = true; state.selected.clear(); renderAllVideos(); },
    }, [el("span", { html: ICON.check }), "Select"]);
    c.appendChild(sortBar(() => navigate("allvideos", 1), selectBtn));
  }
  const first = page * PAGE_SIZE + 1;
  const last = Math.min(data.count, first + data.videos.length - 1);
  c.appendChild(el("div", { class: "section-title" }, `${data.count} videos · showing ${first}–${last}`));
  c.appendChild(videoGrid(data.videos));
  const pg = pager(data.count, PAGE_SIZE, page, (p) => navigate("allvideos", p + 1));
  if (pg) c.appendChild(pg);
}

async function renderVR() {
  setActiveNav("vr");
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "VR")]));
  c.appendChild(el("p", { style: "color:var(--text-dim);margin-top:-8px" },
    "360° and 180° videos, played in an interactive VR viewer."));
  let data;
  try { data = await api.get(`/api/vr?sort=${state.sort.field}&order=${state.sort.order}`); }
  catch (e) { c.appendChild(el("div", { class: "empty" }, [el("h2", {}, "Could not load VR videos")])); return; }
  if (!data.videos.length) {
    c.appendChild(el("div", { class: "empty" }, [
      el("h2", {}, "No VR videos"),
      el("p", {}, "VR content is detected from filenames (360, 180, VR, SBS…). You can also mark any video as VR on its page."),
    ]));
    return;
  }
  if (state.selectMode) {
    c.appendChild(selectToolbar(renderVR));
  } else {
    const selectBtn = el("button", {
      class: "btn", onClick: () => { state.selectMode = true; state.selected.clear(); renderVR(); },
    }, [el("span", { html: ICON.check }), "Select"]);
    c.appendChild(sortBar(renderVR, selectBtn));
  }
  c.appendChild(el("div", { class: "section-title" }, `${data.count} VR video${data.count === 1 ? "" : "s"}`));
  c.appendChild(videoGrid(data.videos));
}

async function loadVRNav() {
  try {
    const s = await api.get("/api/stats");
    const nav = document.getElementById("vr-nav");
    if (nav) nav.style.display = s.vr > 0 ? "" : "none";
  } catch (e) {}
}

/* -------------------------------------------------------------- settings */
const SETTINGS_CATS = [
  ["general", "General", `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1l2.1-2.1M17 7l2.1-2.1"/></svg>`],
  ["media", "Media Management", `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none"/></svg>`],
  ["tags", "Tag Management", `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.6 13.4 12 22l-9-9V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5" fill="currentColor" stroke="none"/></svg>`],
  ["ai", "AI Analysis", `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l1.9 4.6 4.6 1.9-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>`],
  ["about", "About", `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 11v5" stroke-linecap="round"/><circle cx="12" cy="7.8" r="1" fill="currentColor" stroke="none"/></svg>`],
];
let settingsCat = "general";

/* ------------------------------------------------- unwritten tag warning */
// Tags assigned in the app live only in the database until they're written
// into filenames. That's easy to forget, and a library restored from the files
// alone would lose them — so surface it in the toolbar.
async function refreshTagWarning() {
  const btn = document.getElementById("tag-warn-btn");
  if (!btn) return;
  let r;
  try { r = await api.get("/api/tags/pending"); }
  catch (e) { btn.style.display = "none"; return; }   // never block the UI over this
  if (!r.enabled || !r.pending) { btn.style.display = "none"; return; }
  const n = r.pending;
  btn.style.display = "";
  btn.title = `${n} video${n === 1 ? " has" : "s have"} tags that aren't written into ` +
    `${n === 1 ? "its filename" : "their filenames"} yet` +
    (r.skipped ? ` (${r.skipped} more can't be written — open Tags settings for why)` : "") +
    ". Click to open Tags settings.";
}

function settingRow(name, hint, control) {
  return el("div", { class: "setting-row" }, [
    el("div", { class: "setting-info" }, [
      el("div", { class: "setting-name" }, name),
      hint ? el("div", { class: "setting-hint" }, hint) : null,
    ]),
    control,
  ]);
}

function toggleSwitch(checked, onChange) {
  const input = el("input", { type: "checkbox" });
  input.checked = checked;
  input.addEventListener("change", () => onChange(input.checked));
  return el("label", { class: "switch" }, [input, el("span", { class: "track" }), el("span", { class: "thumb" })]);
}

async function renderSettings() {
  state.view = "settings";
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  highlightTree();
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "Settings")]));

  const cats = el("div", { class: "settings-cats" });
  SETTINGS_CATS.forEach(([key, label, icon]) => {
    cats.appendChild(el("div", {
      class: "settings-cat" + (settingsCat === key ? " active" : ""),
      onClick: () => { settingsCat = key; renderSettings(); },
    }, [el("span", { html: icon }), label]));
  });

  const body = el("div", { class: "settings-body" });
  if (settingsCat === "general") await settingsGeneral(body);
  else if (settingsCat === "media") settingsMedia(body);
  else if (settingsCat === "tags") await settingsTags(body);
  else if (settingsCat === "about") await settingsAbout(body);
  else if (settingsCat === "ai") await settingsAI(body);

  c.appendChild(el("div", { class: "settings-wrap" }, [cats, body]));
}

async function settingsGeneral(body) {
  body.appendChild(el("h2", { class: "settings-section-title" }, "General"));
  body.appendChild(el("p", { class: "settings-section-desc" }, "Appearance and playback preferences, remembered in this browser."));

  const themeSel = el("select", { class: "theme", id: "theme-select" },
    THEMES.map(([v, label]) => el("option", { value: v, ...(state.theme === v ? { selected: "" } : {}) }, label)));
  themeSel.addEventListener("change", (e) => applyTheme(e.target.value));
  body.appendChild(settingRow("Theme", "Switch the color scheme.", themeSel));

  body.appendChild(settingRow("Autoplay", "Automatically start a video when you open its page.",
    toggleSwitch(state.autoplay, (on) => {
      state.autoplay = on;
      prefSet("autoplay", on ? "1" : "0");
      toast(on ? "Autoplay enabled" : "Autoplay disabled");
    })));

  // Channel order is a server setting, not a browser pref: the play queue and
  // its "already played this lap" state live in the database.
  const orderSel = el("select", { class: "theme" }, [
    el("option", { value: "random" }, "Random"),
    el("option", { value: "sequential" }, "Sequential (by filename)"),
  ]);
  try {
    const cfg = await api.get("/api/settings");
    orderSel.value = cfg.channel_order === "sequential" ? "sequential" : "random";
  } catch (e) { orderSel.value = "random"; }
  orderSel.addEventListener("change", async (e) => {
    const value = e.target.value;
    try {
      await api.post("/api/settings", { key: "channel_order", value });
      toast(value === "sequential"
        ? "Channels play in filename order" : "Channels play in random order");
    } catch (err) { toast("Could not save that setting"); }
  });
  body.appendChild(settingRow("Channel order",
    "How channel mode picks the next video. Sequential follows filename order; either way each video plays once before the channel loops.",
    orderSel));

  await settingsBackup(body);
  settingsDebug(body);
}

function settingsDebug(body) {
  body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "Debugging"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "Extra diagnostics for troubleshooting. Off by default — leave these alone unless you're chasing a problem."));

  body.appendChild(settingRow("VR playback report",
    "After each immersive VR session, collect timing details (refresh rate, frame cadence, decode rate, buffer depth) and show them below the player. Useful for diagnosing stutter; adds no overhead when off.",
    toggleSwitch(state.vrDebug, (on) => {
      state.vrDebug = on;
      prefSet("vr-debug", on ? "1" : "0");
      toast(on ? "VR playback report enabled" : "VR playback report disabled");
    })));
}

/* -------------------------------------------------------- settings: backup */

function backupSummary(st) {
  if (!st || !st.exists) return "No backup yet.";
  const bits = [`Last backup ${fmtDate(st.created_at)}`, fmtBytes(st.size)];
  const n = (st.counts || {}).videos;
  if (n != null) bits.push(`${n} video${n === 1 ? "" : "s"}`);
  if (st.includes_thumbnails) bits.push("with thumbnails");
  return bits.join(" · ");
}

function triggerBackupDownload() {
  const a = el("a", { href: "/api/backup/download", download: "" });
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 2000);
}

async function settingsBackup(body) {
  body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "Backup"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "Save everything the app knows about your library — tags, favorites, watch history, " +
    "resume points, channels, VR flags, AI summaries and settings — plus config.json. " +
    "Your video files are never copied or modified."));

  let st, settings;
  try { st = await api.get("/api/backup"); } catch (e) { st = { exists: false, path: "" }; }
  try { settings = await api.get("/api/settings"); } catch (e) { settings = {}; }

  const summary = el("div", { style: "margin-top:6px;color:var(--text-dim)" }, backupSummary(st));
  const dlBtn = el("button", {
    class: "btn",
    onClick: () => { triggerBackupDownload(); toast("Downloading last backup…"); },
  }, [el("span", { html: ICON.download }), "Download last"]);
  dlBtn.disabled = !st.exists;

  const runBtn = el("button", { class: "btn primary" },
    [el("span", { html: ICON.download }), el("span", {}, "Back up now")]);
  const runLabel = runBtn.lastChild;
  runBtn.addEventListener("click", async () => {
    runBtn.disabled = dlBtn.disabled = true;
    runLabel.textContent = "Backing up…";
    let r;
    try { r = await api.post("/api/backup"); }
    catch (e) { toast(e.message || "Backup failed"); }
    finally {
      runBtn.disabled = false;
      runLabel.textContent = "Back up now";
    }
    if (!r) { dlBtn.disabled = !st.exists; return; }
    st = r;
    summary.textContent = backupSummary(r);
    dlBtn.disabled = false;
    toast(`Backup saved (${fmtBytes(r.size)})`);
    triggerBackupDownload();
  });

  const hint = el("div", {}, [
    el("div", {}, st.path
      ? `Written to ${st.path} on the server, replacing the previous backup. A copy downloads to this browser.`
      : "Written to the server's data folder, replacing the previous backup. A copy downloads to this browser."),
    summary,
  ]);
  body.appendChild(settingRow("Back up library data", hint,
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap" }, [runBtn, dlBtn])));

  body.appendChild(settingRow("Include thumbnails",
    "Adds the cached poster images to the backup. Much larger file — leave this off if you're happy to rebuild them with a force full rescan.",
    toggleSwitch(settings.backup_include_thumbs === "1", async (on) => {
      try { await api.post("/api/settings", { key: "backup_include_thumbs", value: on ? "1" : "0" }); }
      catch (e) { toast("Could not save setting"); return; }
      toast(on ? "Thumbnails included in backups" : "Thumbnails excluded from backups");
    })));
}

function settingsMedia(body) {
  body.appendChild(el("h2", { class: "settings-section-title" }, "Media Management"));
  body.appendChild(el("p", { class: "settings-section-desc" }, "Keep your library in sync and free of duplicates."));

  const rescanBtn = el("button", { class: "btn primary", onClick: () => reindex() },
    [el("span", { html: ICON.refresh }), "Rescan now"]);
  const forceBtn = el("button", {
    class: "btn",
    onClick: async () => { try { await api.post("/api/reindex?force=true"); toast("Full rescan started…"); pollIndex(); } catch (e) { toast("Could not start rescan"); } },
  }, "Force full rescan");
  body.appendChild(settingRow("Rescan library",
    "Pick up newly added or removed files. A full rescan re-probes every file's metadata.",
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap" }, [rescanBtn, forceBtn])));

  body.appendChild(settingRow("Duplicate videos",
    "Find videos with identical contents, the same file size, or the same length — and clean them up.",
    el("button", { class: "btn", onClick: () => navigate("duplicates") },
      [el("span", { html: ICON.film }), "Find duplicates"])));

  body.appendChild(settingRow("Reset watched status",
    "Remove the Watched flag (the badge on cards) from every video. The Watched page is driven by play history, which this keeps.",
    el("button", {
      class: "btn",
      onClick: async () => {
        if (!confirm(
          "Remove the Watched flag from ALL videos?\n\n" +
          "Every video will show as not watched again. Play history and " +
          "resume points are kept.\n\nThis cannot be undone."
        )) return;
        let r;
        try { r = await api.post("/api/watched/clear"); }
        catch (e) { toast("Could not clear watched flags"); return; }
        toast(r.cleared
          ? `Cleared ${r.cleared} watched flag${r.cleared === 1 ? "" : "s"}`
          : "No videos were flagged as watched");
      },
    }, [el("span", { html: ICON.reset }), "Clear watched flags"])));

  body.appendChild(settingRow("Clear play history",
    "Delete every recorded play. Empties the Watched page and resets each video's last-played time and play count. Resume points are kept.",
    el("button", {
      class: "btn",
      onClick: async () => {
        if (!confirm(
          "Delete ALL play history?\n\n" +
          "The Watched page will be emptied and every video's last-played " +
          "time and play count will reset. Resume points are kept.\n\n" +
          "This cannot be undone."
        )) return;
        let r;
        try { r = await api.post("/api/history/clear"); }
        catch (e) { toast("Could not clear play history"); return; }
        toast(r.cleared
          ? `Deleted ${r.cleared} play record${r.cleared === 1 ? "" : "s"}`
          : "Play history was already empty");
      },
    }, [el("span", { html: ICON.trash }), "Clear play history"])));
}

function tagManageRow(t, refresh) {
  const row = el("div", { class: "tag-manage-row" });
  const showView = () => {
    row.innerHTML = "";
    row.append(
      el("span", { class: "tm-name" }, t.name),
      el("span", { class: "tm-count" }, `${t.count}`),
      el("button", { class: "icon-btn", title: "Rename", html: ICON.pencil, onClick: showEdit }),
      el("button", {
        class: "icon-btn danger", title: "Delete", html: ICON.trash,
        onClick: async () => {
          if (!confirm(`Delete tag "${t.name}"? It will be removed from all videos.`)) return;
          try { await api.del(`/api/tags/${encodeURIComponent(t.name)}`); } catch (e) { toast("Could not delete tag"); return; }
          toast("Tag deleted"); loadTree(); refresh();
        },
      }),
    );
  };
  const showEdit = () => {
    const input = el("input", { type: "text" });
    input.value = t.name;
    const save = async () => {
      const nn = input.value.trim();
      if (!nn || nn === t.name) { showView(); return; }
      try { await api.patch(`/api/tags/${encodeURIComponent(t.name)}`, { name: nn }); }
      catch (e) { toast("Could not rename tag"); return; }
      toast("Tag renamed"); loadTree(); refresh();
    };
    row.innerHTML = "";
    row.append(
      input,
      el("button", { class: "icon-btn", title: "Save", html: ICON.check, onClick: save }),
      el("button", { class: "icon-btn", title: "Cancel", html: ICON.close, onClick: showView }),
    );
    input.focus();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); save(); }
      else if (e.key === "Escape") showView();
    });
  };
  showView();
  return row;
}

async function settingsTags(body) {
  // ── Tags from filenames (first: these settings shape the whole library) ──
  body.appendChild(el("h2", { class: "settings-section-title" }, "Tags from filenames"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "Read tags from the bracketed part of a filename. Example: \u201cChannel - Title [TECH OC GUIDE].mp4\u201d becomes the tags TECH, OC and GUIDE (space-separated)."));

  let settings;
  try { settings = await api.get("/api/settings"); }
  catch (e) { settings = { filename_tag_brackets: "square", filename_tags_auto: "0", filename_tags_strip_title: "1" }; }

  const BRACKETS = [["square", "Square brackets  [ ]"], ["round", "Parentheses  ( )"], ["curly", "Curly braces  { }"]];
  const brSel = el("select", { class: "theme" },
    BRACKETS.map(([v, label]) => el("option", { value: v, ...(settings.filename_tag_brackets === v ? { selected: "" } : {}) }, label)));
  brSel.addEventListener("change", async (e) => {
    try { await api.post("/api/settings", { key: "filename_tag_brackets", value: e.target.value }); toast("Delimiter saved"); }
    catch (err) { toast("Could not save"); }
  });
  body.appendChild(settingRow("Tag delimiter", "Which brackets wrap the tag section of the filename.", brSel));

  body.appendChild(settingRow("Hide tags in titles",
    "Drop the bracketed section from the title shown in the library, so \u201cMyvideo - VidTitle [TAG1 TAG2]\u201d displays as \u201cMyvideo - VidTitle\u201d. Files on disk are never renamed, and the full path is still shown on the video page.",
    toggleSwitch(settings.filename_tags_strip_title !== "0", async (on) => {
      try { await api.post("/api/settings", { key: "filename_tags_strip_title", value: on ? "1" : "0" }); }
      catch (e) { toast("Could not save"); return; }
      toast(on ? "Tags hidden in titles" : "Showing full filenames");
    })));

  body.appendChild(settingRow("Auto-tag on scan",
    "Apply filename tags automatically to new files while indexing.",
    toggleSwitch(settings.filename_tags_auto === "1", async (on) => {
      try { await api.post("/api/settings", { key: "filename_tags_auto", value: on ? "1" : "0" }); toast(on ? "Auto-tagging on" : "Auto-tagging off"); }
      catch (e) { toast("Could not save"); }
    })));

  const genBtn = el("button", {
    class: "btn primary",
    onClick: async () => {
      let r;
      try { r = await api.post("/api/tags/from-filenames", { brackets: brSel.value }); }
      catch (e) { toast("Could not generate tags"); return; }
      toast(`Added ${r.tags_added} tag${r.tags_added === 1 ? "" : "s"} across ${r.videos_tagged} video${r.videos_tagged === 1 ? "" : "s"}`);
      loadTree(); renderSettings();
    },
  }, "Generate tags now");
  body.appendChild(settingRow("Apply to whole library now",
    "Scan every existing filename and add its tags right away.", genBtn));

  // ── Write tags back out to disk (the inverse of the above) ──
  const writePanel = el("div", { class: "tag-write-panel", style: "display:none" });
  const writeBtn = el("button", { class: "btn" }, "Preview changes");

  const renderPlan = (plan) => {
    writePanel.innerHTML = "";
    writePanel.style.display = "";
    const c = plan.counts;
    writePanel.appendChild(el("div", { class: "twp-summary" },
      `${c.rename} to rename · ${c.unchanged} already correct · ${c.skipped} skipped`));

    if (!plan.items.length) {
      writePanel.appendChild(el("div", { class: "twp-empty" },
        c.unchanged ? "Every tagged filename is already up to date." : "No tagged videos to write."));
      return;
    }

    const list = el("div", { class: "twp-list" });
    plan.items.forEach((i) => {
      list.appendChild(el("div", { class: "twp-row " + i.status }, [
        el("div", { class: "twp-from" }, i.from),
        el("div", { class: "twp-to" }, i.status === "skipped" ? `skipped — ${i.reason}` : `→ ${i.to}`),
      ]));
    });
    writePanel.appendChild(list);
    if (plan.truncated) {
      writePanel.appendChild(el("div", { class: "twp-empty" }, "Showing the first 400 changes."));
    }

    if (!c.rename) return;
    const confirm = el("button", { class: "btn primary" }, `Rename ${c.rename} file${c.rename === 1 ? "" : "s"}`);
    const cancel = el("button", { class: "btn ghost", onClick: () => { writePanel.style.display = "none"; } }, "Cancel");
    confirm.addEventListener("click", async () => {
      confirm.disabled = cancel.disabled = true;
      confirm.textContent = "Renaming…";
      let r;
      try { r = await api.post("/api/tags/to-filenames", { apply: true }); }
      catch (e) { toast(e.message || "Could not rename files"); confirm.disabled = cancel.disabled = false; return; }
      toast(`Renamed ${r.renamed} file${r.renamed === 1 ? "" : "s"}` +
        (r.failed_count ? ` · ${r.failed_count} failed` : ""));
      if (r.failed_count) {
        writePanel.innerHTML = "";
        writePanel.appendChild(el("div", { class: "twp-summary" },
          `Renamed ${r.renamed}, but ${r.failed_count} could not be renamed:`));
        const fl = el("div", { class: "twp-list" });
        r.failed.forEach((f) => fl.appendChild(el("div", { class: "twp-row skipped" }, [
          el("div", { class: "twp-from" }, f.from),
          el("div", { class: "twp-to" }, f.error),
        ])));
        writePanel.appendChild(fl);
      } else {
        writePanel.style.display = "none";
      }
      loadTree();
    });
    writePanel.appendChild(el("div", { class: "twp-actions" }, [confirm, cancel]));
  };

  writeBtn.addEventListener("click", async () => {
    writeBtn.disabled = true;
    const label = writeBtn.textContent;
    writeBtn.textContent = "Checking…";
    try { renderPlan(await api.post("/api/tags/to-filenames", {})); }
    catch (e) { toast(e.message || "Could not build a preview"); }
    finally { writeBtn.disabled = false; writeBtn.textContent = label; }
  });

  body.appendChild(settingRow("Write tags into filenames",
    "Rename files on disk so each one carries its tags, e.g. \u201cMyvideo - VidTitle.mp4\u201d " +
    "becomes \u201cMyvideo - VidTitle [TAG1 TAG2].mp4\u201d. Any existing tag section is replaced, " +
    "and videos with no tags are left alone. You'll see a preview before anything is renamed. " +
    "Needs the library mounted read/write.",
    writeBtn));
  body.appendChild(writePanel);

  let warnCfg = "1";
  try { warnCfg = (await api.get("/api/settings")).warn_unwritten_tags; } catch (e) {}
  body.appendChild(settingRow("Warn about unwritten tags",
    "Show a warning in the toolbar when tags have been assigned in the app but not yet written into filenames.",
    toggleSwitch(warnCfg !== "0", async (on) => {
      try {
        await api.post("/api/settings", { key: "warn_unwritten_tags", value: on ? "1" : "0" });
        toast(on ? "Unwritten-tag warnings enabled" : "Unwritten-tag warnings disabled");
        refreshTagWarning();
      } catch (e) { toast("Could not save that setting"); }
    })));

  // ── Manage Tags (the per-tag list, kept last since it can be long) ──
  body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "Manage Tags"));
  body.appendChild(el("p", { class: "settings-section-desc" }, "Add, rename, or delete tags across your whole library."));

  let data;
  try { data = await api.get("/api/tags"); } catch (e) { data = { tags: [] }; }
  const list = el("div", { class: "tag-manage-list" });
  if (!data.tags.length) {
    list.appendChild(el("div", { style: "color:var(--text-faint);font-size:13px" }, "No tags yet."));
  } else {
    data.tags.forEach((t) => list.appendChild(tagManageRow(t, renderSettings)));
  }
  body.appendChild(list);

  const input = el("input", { type: "text", placeholder: "New tag name" });
  const addBtn = el("button", {
    class: "btn primary",
    onClick: async () => {
      const n = input.value.trim();
      if (!n) { toast("Type a tag name"); return; }
      try { await api.post("/api/tags", { name: n }); } catch (e) { toast("Could not add tag"); return; }
      input.value = ""; toast("Tag added"); renderSettings();
    },
  }, "Add tag");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addBtn.click(); } });
  body.appendChild(el("div", { class: "tag-add-row" }, [input, addBtn]));
}

/* --------------------------------------------------------- settings: about */

const UPDATE_ERRORS = {
  no_repo: "No repository is configured in about.json, so there's nothing to check against.",
  rate_limited: "GitHub is rate-limiting this address. Try again in a little while.",
  not_found: "That repository has no published releases yet.",
  http_error: "GitHub returned an unexpected response.",
  unreachable: "Couldn't reach GitHub. Check this machine's internet connection.",
};

function aboutLink(label, href) {
  return el("a", { class: "about-link", href, target: "_blank", rel: "noreferrer noopener" }, label);
}

async function settingsAbout(body) {
  let info;
  try { info = await api.get("/api/about"); }
  catch (e) { body.appendChild(el("div", { class: "empty" }, "Could not load project information.")); return; }

  body.appendChild(el("h2", { class: "settings-section-title" }, "About"));
  body.appendChild(el("div", { class: "about-head" }, [
    el("div", { class: "about-name" }, info.name || "LocalChannels"),
    info.version ? el("div", { class: "about-version" }, `Version ${info.version}`) : null,
    info.tagline ? el("div", { class: "about-tagline" }, info.tagline) : null,
  ]));
  if (info.description) {
    body.appendChild(el("p", { class: "settings-section-desc" }, info.description));
  }

  // ── Updates ──
  const result = el("div", { class: "setting-hint", style: "margin-top:6px" }, "Not checked yet.");
  const checkBtn = el("button", { class: "btn primary" },
    [el("span", { html: ICON.refresh }), el("span", {}, "Check for updates")]);
  const label = checkBtn.lastChild;
  checkBtn.addEventListener("click", async () => {
    checkBtn.disabled = true;
    label.textContent = "Checking…";
    let r;
    try { r = await api.get("/api/about/updates?force=true"); }
    catch (e) { r = { error: "unreachable" }; }
    finally { checkBtn.disabled = false; label.textContent = "Check for updates"; }

    result.innerHTML = "";
    if (r.error) {
      result.appendChild(el("span", {}, UPDATE_ERRORS[r.error] || "Could not check for updates."));
    } else if (r.update_available) {
      result.appendChild(el("span", { class: "about-update" },
        `${r.latest} is available — you're on ${r.current}. `));
      if (r.url) result.appendChild(aboutLink("View the release", r.url));
    } else {
      result.appendChild(el("span", {}, `You're up to date (${r.current}).`));
      if (r.url) { result.appendChild(el("span", {}, " ")); result.appendChild(aboutLink("Releases", r.url)); }
    }
  });
  body.appendChild(settingRow("Updates",
    el("span", {}, [
      el("span", {}, "Asks GitHub for the newest release. Nothing is sent anywhere until you press the button."),
      result,
    ]), checkBtn));

  // ── Project details: rows are skipped entirely when unset in about.json ──
  const lic = info.license || {};
  if (lic.name) {
    body.appendChild(settingRow("License", "How this software is licensed.",
      lic.url ? aboutLink(lic.name, lic.url) : el("span", { class: "about-value" }, lic.name)));
  }
  if (info.author) {
    body.appendChild(settingRow("Author", "Who maintains the project.",
      el("span", { class: "about-value" }, info.author)));
  }
  const contact = info.contact || {};
  if (contact.email) {
    body.appendChild(settingRow("Contact", "Get in touch with the maintainer.",
      aboutLink(contact.email, `mailto:${contact.email}`)));
  }
  if (contact.issues) {
    body.appendChild(settingRow("Report a problem",
      "Bugs and feature requests are tracked publicly.",
      aboutLink("Open an issue", contact.issues)));
  }
  if (info.homepage) {
    body.appendChild(settingRow("Project page", "Source code and documentation.",
      aboutLink(info.homepage.replace(/^https?:\/\//, ""), info.homepage)));
  }
  (info.links || []).forEach((l) => {
    if (l && l.url && l.label) {
      body.appendChild(settingRow(l.label, l.description || "", aboutLink("Open", l.url)));
    }
  });
  if (info.credits) {
    body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "Credits"));
    body.appendChild(el("p", { class: "settings-section-desc" }, info.credits));
  }
  if (!lic.name && !info.author && !contact.email && !contact.issues && !info.homepage) {
    body.appendChild(el("p", { class: "settings-section-desc", style: "margin-top:24px" },
      "Licensing and contact details haven't been filled in yet — edit about.json in the project root to add them."));
  }
}

/* ------------------------------------------------------------ settings: AI */
let aiSettingsPollTimer = null;

async function settingsAI(body) {
  body.appendChild(el("h2", { class: "settings-section-title" }, "AI Analysis"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "Generate video summaries with a vision model through any OpenAI-compatible " +
    "endpoint (vLLM, OpenAI, LM Studio, llama.cpp server, …). Frames are sampled " +
    "from each video and sent to the model together with your system prompt."));

  let s;
  try { s = await api.get("/api/settings"); } catch (e) { s = {}; }

  const saveSetting = async (key, value) => {
    try { await api.post("/api/settings", { key, value: String(value) }); return true; }
    catch (e) { toast(e.message || "Could not save"); return false; }
  };
  const textInput = (key, props = {}) => {
    const input = el("input", { type: "text", class: "ai-input", ...props });
    input.value = s[key] ?? "";
    input.addEventListener("change", async () => {
      if (await saveSetting(key, input.value.trim())) toast("Saved");
    });
    return input;
  };
  const numInput = (key, min, max) => {
    const input = el("input", { type: "number", class: "ai-input num", min, max, step: "1" });
    input.value = s[key] ?? "";
    input.addEventListener("change", async () => {
      if (await saveSetting(key, input.value)) toast("Saved");
    });
    return input;
  };

  // ── Endpoint ──
  body.appendChild(settingRow("Endpoint URL",
    "OpenAI-compatible base URL, e.g. http://host:8000/v1 for vLLM.",
    textInput("ai_endpoint", { placeholder: "http://localhost:8000/v1", spellcheck: "false" })));

  const keyInput = textInput("ai_api_key", { placeholder: "(optional — vLLM usually needs none)" });
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  body.appendChild(settingRow("API key", "Sent as a Bearer token if set.", keyInput));

  body.appendChild(settingRow("Model",
    "Model name passed to the API, e.g. Qwen/Qwen2.5-VL-7B-Instruct.",
    textInput("ai_model", { placeholder: "model name", spellcheck: "false" })));

  const testOut = el("span", { class: "ai-test-result" });
  const testBtn = el("button", {
    class: "btn",
    onClick: async () => {
      testOut.textContent = "Testing…";
      testOut.className = "ai-test-result";
      try {
        const r = await api.post("/api/ai/test");
        testOut.textContent = `OK — ${r.model} replied.`;
        testOut.classList.add("ok");
      } catch (e) {
        testOut.textContent = e.message || "Failed";
        testOut.classList.add("err");
      }
    },
  }, [el("span", { html: ICON.check }), "Test connection"]);
  body.appendChild(settingRow("Connection",
    "Send a tiny text-only request to verify the endpoint, model and key.",
    el("div", { class: "ai-test-wrap" }, [testBtn, testOut])));

  // ── Frame sampling ──
  body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "Frame sampling"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "How frames are picked from each video before being sent to the model."));

  const countRow = settingRow("Frames per video",
    "Sampled evenly across the whole video.", numInput("ai_frame_count", 1, 64));
  const intervalRow = settingRow("Seconds between frames",
    "One frame every N seconds, up to the max below.", numInput("ai_frame_interval", 1, 86400));

  const modeSel = el("select", { class: "theme" }, [
    el("option", { value: "count", ...(s.ai_frame_mode !== "interval" ? { selected: "" } : {}) }, "Fixed number of frames"),
    el("option", { value: "interval", ...(s.ai_frame_mode === "interval" ? { selected: "" } : {}) }, "Time between frames"),
  ]);
  const syncModeRows = () => {
    const interval = modeSel.value === "interval";
    countRow.style.display = interval ? "none" : "";
    intervalRow.style.display = interval ? "" : "none";
  };
  modeSel.addEventListener("change", async () => {
    if (await saveSetting("ai_frame_mode", modeSel.value)) toast("Saved");
    syncModeRows();
  });
  body.appendChild(settingRow("Sampling mode",
    "Fixed count spreads frames evenly; time-based follows the video's length.", modeSel));
  body.appendChild(countRow);
  body.appendChild(intervalRow);
  syncModeRows();

  body.appendChild(settingRow("Max frames",
    "Hard cap per video. Keep at or below your server's image-per-prompt limit " +
    "(vLLM: --limit-mm-per-prompt).", numInput("ai_max_frames", 1, 64)));
  body.appendChild(settingRow("Frame width",
    "Frames are downscaled to this width (px) before upload.", numInput("ai_frame_width", 64, 4096)));

  // ── System prompt ──
  body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "System prompt"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "Instructions given to the model with every analysis. Saved when you click away."));
  const promptTA = el("textarea", { class: "ai-prompt", rows: "6", spellcheck: "false" });
  promptTA.value = s.ai_system_prompt ?? "";
  promptTA.addEventListener("change", async () => {
    if (await saveSetting("ai_system_prompt", promptTA.value)) toast("Prompt saved");
  });
  body.appendChild(promptTA);

  // ── Batch analysis ──
  body.appendChild(el("h2", { class: "settings-section-title", style: "margin-top:40px" }, "Batch analysis"));
  body.appendChild(el("p", { class: "settings-section-desc" },
    "Analyze the whole library or a single folder (including its subfolders). " +
    "Videos run one at a time; you can keep using the app while it works."));

  let tree = [];
  try { tree = (await api.get("/api/tree")).folders; } catch (e) {}
  const scopeSel = el("select", { class: "theme" }, [
    el("option", { value: "" }, "Entire library"),
    ...tree.map((f) => el("option", { value: f.path },
      `${"\u00a0\u00a0".repeat(f.depth)}${f.name} (${f.count})`)),
  ]);
  body.appendChild(settingRow("Scope", "What to analyze.", scopeSel));

  let force = false;
  body.appendChild(settingRow("Re-analyze existing",
    "Off: videos that already have a summary are skipped. On: everything is redone.",
    toggleSwitch(false, (on) => { force = on; })));

  const progress = el("div", { class: "ai-progress", style: "display:none" });
  const runBtn = el("button", { class: "btn primary" },
    [el("span", { html: ICON.sparkle }), "Start analysis"]);
  const cancelBtn = el("button", { class: "btn", style: "display:none" },
    [el("span", { html: ICON.close }), "Cancel"]);

  const renderProgress = (st) => {
    if (!st.running && !st.finished_at) { progress.style.display = "none"; return; }
    progress.style.display = "";
    progress.innerHTML = "";
    if (st.running) {
      const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
      progress.appendChild(el("div", { class: "ai-prog-row" }, [
        el("div", { class: "spinner" }),
        el("span", {}, `${st.done} / ${st.total} — ${st.current || "starting…"}`),
      ]));
      progress.appendChild(el("div", { class: "ai-prog-bar" },
        [el("div", { class: "ai-prog-fill", style: `width:${pct}%` })]));
    } else {
      progress.appendChild(el("div", { class: "ai-prog-row" },
        [el("span", {}, `Finished: ${st.done} / ${st.total} analyzed` +
          (st.errors ? `, ${st.errors} failed` : "") + ".")]));
    }
    if (st.errors && st.last_error) {
      progress.appendChild(el("div", { class: "ai-prog-err" }, `Last error: ${st.last_error}`));
    }
  };

  const pollBatch = async () => {
    clearTimeout(aiSettingsPollTimer);
    if (!progress.isConnected) return;
    let st;
    try { st = await api.get("/api/ai/status"); } catch (e) { st = null; }
    if (st) {
      renderProgress(st);
      runBtn.style.display = st.running ? "none" : "";
      cancelBtn.style.display = st.running ? "" : "none";
      if (st.running) aiSettingsPollTimer = setTimeout(pollBatch, 2000);
    }
  };

  runBtn.addEventListener("click", async () => {
    const payload = scopeSel.value ? { folder: scopeSel.value } : { all: true };
    if (force) payload.force = true;
    let r;
    try { r = await api.post("/api/ai/analyze", payload); }
    catch (e) { toast(e.message || "Could not start"); return; }
    if (r.status === "already_running") { toast("A job is already running"); pollBatch(); return; }
    if (r.status === "nothing_to_do") { toast("Nothing to analyze — all videos already have summaries"); return; }
    toast(`Analysis started — ${r.queued} video${r.queued === 1 ? "" : "s"} queued`);
    pollBatch();
  });
  cancelBtn.addEventListener("click", async () => {
    try { await api.post("/api/ai/cancel"); toast("Cancelling after the current video…"); }
    catch (e) { toast("Could not cancel"); }
  });

  body.appendChild(settingRow("Run", "Start or stop the background job.",
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap" }, [runBtn, cancelBtn])));
  body.appendChild(progress);
  pollBatch(); // pick up an already-running job when opening the page
}

/* ------------------------------------------------------------ duplicates */
let dupeMethod = "hash";
let dupeTolerance = 0;   // milliseconds of slack when matching by length
const DUPE_METHODS = [["hash", "Identical files"], ["size", "Same file size"], ["duration", "Same length"]];
const DUPE_TOLERANCES = [
  [0, "Exact (to the ms)"], [10, "±10 ms"], [100, "±100 ms"],
  [500, "±500 ms"], [1000, "±1 second"],
];

async function renderDuplicates() {
  state.view = "duplicates";
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  highlightTree();
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "crumbs" }, [
    el("a", { onClick: () => navigate("settings") }, "Settings"),
    el("span", { class: "sep" }, "/"),
    el("span", {}, "Duplicates"),
  ]));
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "Duplicate videos")]));

  const methodSel = el("select", { class: "theme" },
    DUPE_METHODS.map(([v, label]) => el("option", { value: v, ...(dupeMethod === v ? { selected: "" } : {}) }, label)));
  methodSel.addEventListener("change", (e) => { dupeMethod = e.target.value; renderDuplicates(); });

  // Length matching compares runtimes in milliseconds; the tolerance decides how
  // much drift still counts as a match. Exact is the default because rounding to
  // whole seconds lumps together large numbers of unrelated videos.
  const tolSel = el("select", { class: "theme" },
    DUPE_TOLERANCES.map(([v, label]) =>
      el("option", { value: v, ...(dupeTolerance === v ? { selected: "" } : {}) }, label)));
  tolSel.addEventListener("change", (e) => { dupeTolerance = parseInt(e.target.value, 10); renderDuplicates(); });

  const toolbar = el("div", { class: "dupe-toolbar" }, [
    el("span", { class: "sb-label" }, "Match by"), methodSel,
  ]);
  if (dupeMethod === "duration") {
    toolbar.append(el("span", { class: "sb-label" }, "Tolerance"), tolSel);
  }
  toolbar.appendChild(el("span", { style: "color:var(--text-faint);font-size:12.5px" },
    dupeMethod === "hash" ? "Byte-for-byte identical files."
      : dupeMethod === "size" ? "Videos sharing an exact file size."
      : dupeTolerance === 0 ? "Runtimes that match to the millisecond."
      : `Runtimes within ${dupeTolerance >= 1000 ? `${dupeTolerance / 1000} s` : `${dupeTolerance} ms`} of each other.`));
  c.appendChild(toolbar);

  const holder = el("div", {});
  holder.appendChild(el("div", { class: "empty" }, [
    el("div", { class: "spinner", style: "margin:0 auto" }),
    el("p", {}, dupeMethod === "hash" ? "Scanning and hashing files… this can take a moment the first time." : "Scanning…"),
  ]));
  c.appendChild(holder);

  let data;
  try { data = await api.get(`/api/duplicates?method=${dupeMethod}&tolerance_ms=${dupeTolerance}`); }
  catch (e) { holder.innerHTML = ""; holder.appendChild(el("div", { class: "empty" }, [el("h2", {}, "Scan failed")])); return; }

  holder.innerHTML = "";
  if (!data.groups.length) {
    holder.appendChild(el("div", { class: "empty" }, [
      el("div", { html: ICON.check }),
      el("h2", {}, "No duplicates found"),
      el("p", {}, "Nothing matched by this method."),
    ]));
    return;
  }
  const total = data.groups.reduce((n, g) => n + g.videos.length, 0);
  holder.appendChild(el("p", { style: "color:var(--text-dim);margin:-4px 0 18px" },
    `${data.groups.length} group${data.groups.length === 1 ? "" : "s"} · ${total} videos`));

  data.groups.forEach((g) => {
    const spread = (g.duration_max != null && g.duration_min != null)
      ? Math.round((g.duration_max - g.duration_min) * 1000) : 0;
    const head = el("div", { class: "dupe-group-head" }, [
      el("span", {}, `${g.videos.length} copies`),
      g.size ? el("span", {}, `· ${fmtBytes(g.size)}`) : null,
      g.duration != null ? el("span", {}, `· ${fmtDurationMs(g.duration)}`) : null,
      spread > 0 ? el("span", { class: "dg-spread" }, `· spread ${spread} ms`) : null,
    ]);
    const group = el("div", { class: "dupe-group" }, [head]);
    g.videos.forEach((v) => {
      const thumb = v.has_thumb
        ? el("img", { src: `/api/thumb/${v.id}`, loading: "lazy", alt: "" })
        : el("div", { class: "ph" });
      const del = el("button", {
        class: "btn ghost", style: "color:var(--danger)",
        onClick: async () => {
          if (!confirm(`Delete this file from disk?\n\n${v.rel_path}\n\nThis cannot be undone.`)) return;
          try { await api.del(`/api/video/${v.id}/file`); } catch (e) { toast("Could not delete file"); return; }
          toast("File deleted"); loadTree(); renderDuplicates();
        },
      }, [el("span", { html: ICON.trash }), "Delete file"]);
      const remove = el("button", {
        class: "btn",
        title: "Remove from the library but keep the file on disk",
        onClick: async () => {
          if (!confirm(`Remove from the library but keep the file on disk?\n\n${v.rel_path}\n\nIt won't reappear on the next rescan.`)) return;
          try { await api.post(`/api/video/${v.id}/remove`); } catch (e) { toast("Could not remove"); return; }
          toast("Removed from library"); loadTree(); renderDuplicates();
        },
      }, "Remove from library");
      group.appendChild(el("div", { class: "dupe-item" }, [
        thumb,
        el("div", { class: "di-info" }, [
          el("div", { class: "card-title", title: v.title }, v.title),
          el("div", { class: "di-path" }, v.rel_path),
          el("div", { class: "di-meta" }, [
            v.duration != null ? fmtDurationMs(v.duration) : "\u2014",
            v.size ? ` \u00b7 ${fmtBytes(v.size)}` : "",
            resLabel(v.width, v.height) ? ` \u00b7 ${resLabel(v.width, v.height)}` : "",
          ].join("")),
        ]),
        el("button", { class: "btn", onClick: () => navigate("video", v.id) }, "Open"),
        remove,
        del,
      ]));
    });
    holder.appendChild(group);
  });
}

async function renderTags() {
  setActiveNav("tags");
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "Tags")]));
  const data = await api.get("/api/tags");

  // Always offer the untagged list — it's most useful precisely when there are
  // few or no tags yet and you're working through the library.
  if (data.untagged) {
    c.appendChild(el("div", { class: "untagged-card", onClick: () => navigate("untagged") }, [
      el("span", { class: "uc-icon", html: ICON.tag }),
      el("div", {}, [
        el("div", { class: "uc-title" }, "Untagged videos"),
        el("div", { class: "uc-sub" },
          `${data.untagged} video${data.untagged === 1 ? "" : "s"} with no tags yet`),
      ]),
    ]));
  }

  if (!data.tags.length) {
    c.appendChild(el("div", { class: "empty" }, [
      el("h2", {}, "No tags yet"),
      el("p", {}, "Open any video and add tags — they'll appear here."),
    ]));
    return;
  }
  c.appendChild(el("p", { style: "color:var(--text-dim);margin-top:14px" }, "Click a tag to see the videos tagged with it."));
  const wrap = el("div", { class: "tag-pick", style: "max-width:900px" });
  data.tags.forEach((t) => {
    wrap.appendChild(el("div", {
      class: "chip", onClick: () => navigate("tag", t.name),
      title: `View videos tagged “${t.name}”`,
    }, `${t.name}  ·  ${t.count}`));
  });
  c.appendChild(wrap);
}

async function renderUntagged() {
  state.view = "untagged";
  setActiveNav("tags");
  const c = main();
  c.innerHTML = `<div class="empty"><div class="spinner" style="margin:0 auto"></div></div>`;
  let data;
  try { data = await api.get(`/api/untagged?sort=${state.sort.field}&order=${state.sort.order}`); }
  catch (e) { c.innerHTML = ""; c.appendChild(el("div", { class: "empty" }, "Could not load untagged videos.")); return; }

  c.innerHTML = "";
  c.appendChild(el("div", { class: "crumbs" }, [
    el("a", { onClick: () => navigate("tags") }, "Tags"),
    el("span", { class: "sep" }, "/"),
    el("span", {}, "Untagged"),
  ]));
  c.appendChild(el("div", { class: "page-head" }, [
    el("h1", { class: "page-title" }, "Untagged"),
  ]));

  if (!data.videos.length) {
    c.appendChild(el("div", { class: "empty" }, [
      el("h2", {}, "Everything is tagged"),
      el("p", {}, "Every video in your library has at least one tag."),
    ]));
    return;
  }

  // Select mode matters here: this page is where you'd bulk-tag a backlog.
  if (state.selectMode) {
    c.appendChild(selectToolbar(renderUntagged));
  } else {
    c.appendChild(sortBar(renderUntagged,
      el("button", { class: "btn", onClick: () => { state.selectMode = true; state.selected.clear(); renderUntagged(); } },
        [el("span", { html: ICON.check }), "Select"])));
  }
  c.appendChild(el("div", { class: "section-title" },
    `${data.total} video${data.total === 1 ? "" : "s"} with no tags`));
  c.appendChild(videoGrid(data.videos));
}

async function renderTagVideos(name) {
  state.view = "tag";
  setActiveNav("tags");
  const c = main();
  c.innerHTML = `<div class="empty"><div class="spinner" style="margin:0 auto"></div></div>`;
  let data;
  try { data = await api.get(`/api/tag/${encodeURIComponent(name)}`); }
  catch (e) { c.innerHTML = `<div class="empty"><h2>Could not load tag</h2></div>`; return; }

  c.innerHTML = "";
  c.appendChild(el("div", { class: "crumbs" }, [
    el("a", { onClick: () => navigate("tags") }, "Tags"),
    el("span", { class: "sep" }, "/"),
    el("span", {}, `#${name}`),
  ]));
  c.appendChild(el("div", { class: "page-head" }, [
    el("h1", { class: "page-title" }, `#${name}`),
  ]));

  if (!data.videos.length) {
    c.appendChild(el("div", { class: "empty" }, [el("h2", {}, "No videos with this tag")]));
    return;
  }

  const rerender = () => renderTagVideos(name);
  if (state.selectMode) {
    c.appendChild(selectToolbar(rerender));
  } else {
    const bar = el("div", { class: "sortbar" }, [
      el("button", { class: "btn primary", onClick: () => startChannel("", [name]) },
        [el("span", { html: ICON.play }), "Play as channel"]),
      el("div", { class: "spacer" }),
      viewToggle(rerender),
      el("button", { class: "btn", onClick: () => { state.selectMode = true; state.selected.clear(); rerender(); } },
        [el("span", { html: ICON.check }), "Select"]),
    ]);
    c.appendChild(bar);
  }
  c.appendChild(el("div", { class: "section-title" }, `${data.videos.length} video${data.videos.length === 1 ? "" : "s"}`));
  c.appendChild(videoGrid(data.videos));
}

/* ---------------------------------------------------------- channel mode */
async function renderChannelSetup() {
  state.view = "channelSetup";
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  highlightTree();
  const c = main();
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [el("h1", { class: "page-title" }, "Create a channel")]));

  const setup = el("div", { class: "channel-setup" });
  const box = el("div", { class: "card-lg" });
  box.appendChild(el("p", { style: "margin-top:0;color:var(--text-dim)" },
    "A channel plays videos at random without repeats, then loops once everything has aired. Combine any folders and tags — a video is included if it's in one of the folders or has one of the tags."));

  // name
  box.appendChild(el("label", {}, "Channel name"));
  const nameInput = el("input", {
    type: "text", placeholder: "e.g. Background Cooking",
    class: "theme", style: "width:100%;height:40px;margin-bottom:20px;padding:0 12px",
  });
  box.appendChild(nameInput);

  // folders (multi)
  box.appendChild(el("label", {}, "Folders (includes subfolders)"));
  const folderList = el("div", { class: "folder-check-list", style: "margin-bottom:20px" });
  const selectedFolders = new Set();
  const mkFolderCheck = (value, label, indent) => {
    const cb = el("input", { type: "checkbox" });
    cb.addEventListener("change", () => {
      if (cb.checked) selectedFolders.add(value); else selectedFolders.delete(value);
    });
    if (state.path && value === state.path) { cb.checked = true; selectedFolders.add(value); }
    return el("label", { class: "folder-check", style: `padding-left:${8 + indent * 14}px` }, [cb, label]);
  };
  folderList.appendChild(mkFolderCheck("", "Entire library", 0));
  state.tree.forEach((f) => folderList.appendChild(mkFolderCheck(f.path, `${f.name} (${f.count})`, f.depth + 1)));
  box.appendChild(folderList);

  // tags (multi)
  box.appendChild(el("label", {}, "Tags (optional)"));
  const tagWrap = el("div", { class: "tag-pick" });
  const selectedTags = new Set();
  try {
    const td = await api.get("/api/tags");
    if (!td.tags.length) tagWrap.appendChild(el("span", { style: "color:var(--text-faint);font-size:13px" }, "No tags defined yet."));
    td.tags.forEach((t) => {
      const chip = el("div", { class: "chip" }, `${t.name} (${t.count})`);
      chip.addEventListener("click", () => {
        if (selectedTags.has(t.name)) { selectedTags.delete(t.name); chip.classList.remove("on"); }
        else { selectedTags.add(t.name); chip.classList.add("on"); }
      });
      tagWrap.appendChild(chip);
    });
  } catch (e) {}
  box.appendChild(tagWrap);

  const createChannel = async (thenPlay) => {
    const name = nameInput.value.trim();
    const folders = [...selectedFolders];
    const tags = [...selectedTags];
    if (!name) { toast("Give the channel a name"); return; }
    if (!folders.length && !tags.length) { toast("Pick at least one folder or tag"); return; }
    let res;
    try { res = await api.post("/api/channels", { name, folders, tags }); }
    catch (e) { toast(e.message || "Could not create channel"); return; }
    await loadChannels();
    toast(`Channel “${name}” created`);
    if (thenPlay) startSavedChannel(res.id);
    else navigate("browse", "");
  };

  box.appendChild(el("div", { class: "row-btns" }, [
    el("button", { class: "btn primary", style: "height:44px;padding:0 22px", onClick: () => createChannel(false) },
      [el("span", { html: ICON.tv }), "Create channel"]),
    el("button", { class: "btn", style: "height:44px;padding:0 22px", onClick: () => createChannel(true) },
      [el("span", { html: ICON.play }), "Create & play"]),
  ]));

  setup.appendChild(box);
  c.appendChild(setup);
}

async function startChannel(folder, tags) {
  const tagStr = (tags || []).join(",");
  let data;
  try {
    // reset so a freshly started channel begins a clean rotation
    await api.post(`/api/channel/reset?folder=${encodeURIComponent(folder)}&tags=${encodeURIComponent(tagStr)}`);
    data = await api.get(`/api/channel/next?folder=${encodeURIComponent(folder)}&tags=${encodeURIComponent(tagStr)}`);
  } catch (e) {
    toast(e.message || "No videos match this channel");
    return;
  }
  state.channel = { folder, tags: tags || [], key: data.channel_key, poolSize: data.pool_size };
  renderChannelPlayer(data);
}

function renderChannelPlayer(data) {
  const root = document.getElementById("channel-root");
  root.innerHTML = "";
  const v = data.video;
  state.channel.current = v;
  state.channel.remaining = data.remaining;
  if (data.looped) toast("Channel looped — replaying everything");

  const video = el("video", { src: `/api/stream/${v.id}`, autoplay: "", controls: "" });

  const bug = el("div", { class: "channel-bug" }, [
    el("span", { class: "on-air" }, [el("span", { class: "dot" }), "ON AIR"]),
    el("span", { class: "mono" }, channelLabel()),
  ]);

  const info = el("div", { class: "channel-info" }, [
    el("div", {}, [
      el("div", { class: "ci-title" }, v.title),
      el("div", { class: "ci-sub" }, `${resLabel(v.width, v.height) || ""} ${v.fps ? "· " + Math.round(v.fps) + "fps" : ""} · ${state.channel.remaining} left in rotation`),
    ]),
    el("div", { class: "channel-ctrls" }, [
      btnRound(ICON.next, "Next", nextChannel),
    ]),
  ]);

  const closeBtn = el("button", {
    class: "btn channel-close", onClick: closeChannel,
  }, [el("span", { html: ICON.close }), "Exit"]);

  video.addEventListener("ended", nextChannel);

  // Chrome (bug, info overlay, exit button) shows on mouse move and fades
  // together when idle, for a clean full-screen channel experience.
  const chrome = [bug, info, closeBtn];
  let hideTimer;
  const showChrome = () => {
    chrome.forEach((el0) => el0.classList.remove("ch-hidden"));
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => chrome.forEach((el0) => el0.classList.add("ch-hidden")), 3000);
  };
  const container = el("div", { class: "channel", onMousemove: showChrome }, [
    video, bug, info, closeBtn,
  ]);
  root.appendChild(container);
  showChrome();

  document.addEventListener("keydown", channelKeys);
}

function channelLabel() {
  const ch = state.channel;
  if (!ch) return "";
  if (ch.name) return ch.name;
  const parts = [];
  if (ch.folder) parts.push(ch.folder.split("/").pop());
  if (ch.tags && ch.tags.length) parts.push("#" + ch.tags.join(" #"));
  if (!parts.length) parts.push("All");
  return parts.join(" · ");
}

function btnRound(icon, title, onClick) {
  return el("button", { class: "cbtn", title, onClick }, [el("span", { html: icon })]);
}

async function startSavedChannel(id) {
  let data;
  try {
    await api.post(`/api/channels/${id}/reset`);
    data = await api.get(`/api/channels/${id}/next`);
  } catch (e) {
    toast(e.message || "This channel has no videos");
    return;
  }
  state.channel = { savedId: id, name: data.name };
  renderChannelPlayer(data);
}

async function nextChannel() {
  const ch = state.channel;
  if (!ch) return;
  const cur = ch.current ? ch.current.id : "";
  try {
    let data;
    if (ch.savedId != null) {
      data = await api.get(`/api/channels/${ch.savedId}/next?exclude=${cur}`);
    } else {
      const tagStr = (ch.tags || []).join(",");
      data = await api.get(
        `/api/channel/next?folder=${encodeURIComponent(ch.folder || "")}&tags=${encodeURIComponent(tagStr)}&exclude=${cur}`
      );
    }
    renderChannelPlayer(data);
  } catch (e) {
    toast("Channel ended");
    closeChannel();
  }
}

function channelKeys(e) {
  if (e.key === "Escape") closeChannel();
  else if (e.key === "n" || e.key === "ArrowRight") nextChannel();
}

function closeChannel() {
  document.removeEventListener("keydown", channelKeys);
  document.getElementById("channel-root").innerHTML = "";
  state.channel = null;
}

/* ------------------------------------------------------------- search */
let searchTimer;
async function renderSearch(q) {
  state.view = "search";
  const c = main();
  c.innerHTML = `<div class="empty"><div class="spinner" style="margin:0 auto"></div></div>`;
  // Client-side filter over a full browse of the library root is heavy; instead
  // we scan the flat video list via the tree's counts is not enough — do a
  // dedicated fetch of everything under root and filter.
  const data = await api.get(`/api/browse?path=`);
  // gather all videos recursively by walking tree folders
  const all = [];
  const seen = new Set();
  const collect = async (path) => {
    const d = await api.get(`/api/browse?path=${encodeURIComponent(path)}`);
    d.videos.forEach((v) => { if (!seen.has(v.id)) { seen.add(v.id); all.push(v); } });
    for (const f of d.folders) await collect(f.path);
  };
  await collect("");
  const ql = q.toLowerCase();
  const matches = all.filter((v) =>
    v.title.toLowerCase().includes(ql) || v.rel_path.toLowerCase().includes(ql)
  );
  c.innerHTML = "";
  c.appendChild(el("div", { class: "page-head" }, [
    el("h1", { class: "page-title" }, `Search: “${q}”`),
  ]));
  c.appendChild(el("p", { style: "color:var(--text-dim);margin-top:-8px" }, `${matches.length} result${matches.length === 1 ? "" : "s"}`));
  if (!matches.length) {
    c.appendChild(el("div", { class: "empty" }, [el("h2", {}, "No matches")]));
    return;
  }
  c.appendChild(viewBar(() => renderSearch(q)));
  c.appendChild(videoGrid(matches));
}

/* ---------------------------------------------------------- indexing UI */
async function indexBanner() {
  const status = await api.get("/api/index/status").catch(() => ({ running: false }));
  if (!status.running) return document.createComment("");
  const bar = el("div", { class: "index-banner" }, [
    el("div", { class: "spinner" }),
    el("span", {}, `Indexing… ${status.done}/${status.total}`),
    el("div", { class: "progress" }, [
      el("div", { style: `width:${status.total ? (status.done / status.total) * 100 : 0}%` }),
    ]),
  ]);
  pollIndex();
  return bar;
}

let indexPoll;
// "Library updated · 2 added · 3 moved" — moves are worth calling out, since
// they're the difference between keeping a video's history and losing it.
function scanSummary(last) {
  if (!last) return "Library updated";
  const bits = [];
  if (last.added) bits.push(`${last.added} added`);
  if (last.moved) bits.push(`${last.moved} renamed or moved`);
  if (last.updated) bits.push(`${last.updated} updated`);
  if (last.removed) bits.push(`${last.removed} removed`);
  return bits.length ? `Library updated · ${bits.join(" · ")}` : "Library up to date";
}

function pollIndex() {
  clearInterval(indexPoll);
  indexPoll = setInterval(async () => {
    const s = await api.get("/api/index/status").catch(() => ({ running: false }));
    if (!s.running) {
      clearInterval(indexPoll);
      toast(scanSummary(s.last));
      loadTree();
      loadVRNav();
      if (state.view === "browse") renderBrowse();
    }
  }, 1500);
}

async function reindex() {
  await api.post("/api/reindex");
  toast("Rescanning library…");
  refreshTagWarning();   // a scan can add or remove tagged files
  if (state.view === "browse") renderBrowse();
  else pollIndex();
}

function emptyLibrary() {
  return el("div", { class: "empty" }, [
    el("div", { html: ICON.film }),
    el("h2", {}, "Your library is empty"),
    el("p", {}, ["Point ", el("code", {}, "root_path"), " in config.json at your videos, then click rescan."]),
    el("button", { class: "btn primary", style: "margin-top:16px", onClick: reindex }, "Rescan now"),
  ]);
}

/* --------------------------------------------------------------- tree/nav */
function persistExpanded() {
  prefSet("expanded", JSON.stringify([...state.treeExpanded]));
}

async function loadTree() {
  const data = await api.get("/api/tree").catch(() => ({ folders: [] }));
  state.tree = data.folders;
  const tree = document.getElementById("tree");
  tree.innerHTML = "";
  if (!data.folders.length) {
    tree.appendChild(el("div", { style: "padding:8px 10px;color:var(--text-faint);font-size:13px" }, "No folders"));
    return;
  }
  // Build a nested structure from the flat, path-sorted folder list.
  const nodes = new Map();
  data.folders.forEach((f) => nodes.set(f.path, { ...f, children: [] }));
  const roots = [];
  data.folders.forEach((f) => {
    const parentPath = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
    const parent = nodes.get(parentPath);
    if (parent) parent.children.push(nodes.get(f.path));
    else roots.push(nodes.get(f.path));
  });

  const renderNode = (node, depth) => {
    const hasChildren = node.children.length > 0;
    const open = state.treeExpanded.has(node.path);
    const twist = el("span", {
      class: "twist" + (hasChildren ? (open ? " open" : "") : " leaf"),
      html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 6l6 6-6 6"/></svg>`,
      onClick: (ev) => {
        ev.stopPropagation();
        if (!hasChildren) return;
        if (open) state.treeExpanded.delete(node.path);
        else state.treeExpanded.add(node.path);
        persistExpanded();
        loadTree();
      },
    });
    const row = el("div", {
      class: "tree-row", "data-path": node.path,
      style: `padding-left:${depth * 14}px`,
      onClick: () => go("browse", node.path),
    }, [
      twist,
      el("span", { class: "fold", html: ICON.folder }),
      el("span", { class: "name", title: node.name }, node.name),
      el("span", { class: "count" }, String(node.count)),
    ]);
    tree.appendChild(row);
    if (hasChildren && open) node.children.forEach((ch) => renderNode(ch, depth + 1));
  };
  roots.forEach((n) => renderNode(n, 0));
  highlightTree();
}

async function loadChannels() {
  const data = await api.get("/api/channels").catch(() => ({ channels: [] }));
  state.channels = data.channels;
  const box = document.getElementById("channels");
  box.innerHTML = "";
  if (!state.channels.length) {
    box.appendChild(el("div", { style: "padding:6px 10px;color:var(--text-faint);font-size:12.5px" }, "No channels yet"));
    return;
  }
  state.channels.forEach((ch) => {
    const item = el("div", { class: "chan-item", onClick: () => startSavedChannel(ch.id) }, [
      el("span", { class: "ci-ic", html: ICON.tv }),
      el("span", { class: "ci-name", title: ch.name }, ch.name),
      el("span", { class: "ci-count" }, String(ch.count)),
      el("span", {
        class: "ci-del", title: "Delete channel",
        onClick: async (ev) => {
          ev.stopPropagation();
          if (!confirm(`Delete channel "${ch.name}"?`)) return;
          await api.del(`/api/channels/${ch.id}`);
          loadChannels();
        },
      }, "×"),
    ]);
    box.appendChild(item);
  });
}

function highlightTree() {
  document.querySelectorAll(".tree-row").forEach((n) =>
    n.classList.toggle("active", state.view === "browse" && n.getAttribute("data-path") === state.path));
}

function setActiveNav(view, path = null) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((n) => {
    if (n.getAttribute("data-view") === view && view === "browse") {
      n.classList.toggle("active", path === "");
    } else {
      n.classList.toggle("active", n.getAttribute("data-view") === view);
    }
  });
  highlightTree();
}

/* ------------------------------------------------------------------ router
   Hash-based routing so the browser Back/Forward buttons move between pages
   inside the app instead of leaving the site entirely. */
function buildHash(type, arg) {
  switch (type) {
    case "video":   return "#/video/" + encodeURIComponent(String(arg));
    case "allvideos": {
      const n = parseInt(arg, 10) || 1;   // 1-based; page 1 stays a bare hash
      return n > 1 ? "#/allvideos/" + n : "#/allvideos";
    }
    case "vr":      return "#/vr";
    case "settings": return "#/settings";
    case "duplicates": return "#/duplicates";
    case "history": return "#/history";
    case "new":     return "#/new";
    case "favorites": return "#/favorites";
    case "tags":    return "#/tags";
    case "untagged": return "#/untagged";
    case "tag":     return "#/tag/" + encodeURIComponent(arg || "");
    case "channel":
    case "channelSetup": return "#/channel";
    case "search":  return "#/search/" + encodeURIComponent(arg || "");
    case "browse":
    default:        return "#/browse/" + encodeURIComponent(arg || "");
  }
}

/* ---------------------------------------------------------- scroll memory */
// The scrolling element is .main, not the window, so the browser's own scroll
// restoration never applied here. Views also render asynchronously, so there
// is nothing to scroll to at the moment the hash changes. Positions are kept
// per hash and reapplied once the new view is in the DOM.
const scrollMem = new Map();
let scrollKey = location.hash || "#/";
let restoringScroll = false;
// Set by navigate(); any hashchange that arrives without it came from Back,
// Forward, or a hand-edited URL — the cases where the position should return.
let freshNavigation = false;

function mainEl() { return document.getElementById("main"); }

function rememberScroll() {
  const m = mainEl();
  if (m && !restoringScroll) scrollMem.set(scrollKey, m.scrollTop);
}

function restoreScroll(top) {
  const m = mainEl();
  if (!m) return;
  restoringScroll = true;
  m.scrollTop = top;
  // Thumbnails and posters settle after first paint and can shorten the page,
  // clamping the assignment above. Reapply over the next few frames, stopping
  // as soon as the user takes over.
  let tries = 0;
  const settle = () => {
    if (!restoringScroll) return;
    if (Math.abs(m.scrollTop - top) > 1 && m.scrollHeight > top) m.scrollTop = top;
    if (++tries < 12) requestAnimationFrame(settle);
    else restoringScroll = false;
  };
  requestAnimationFrame(settle);
  setTimeout(() => { restoringScroll = false; }, 600);
}

function navigate(type, arg = "", replace = false) {
  const hash = buildHash(type, arg);
  rememberScroll();                                       // leaving: keep our place
  freshNavigation = true;
  if (location.hash === hash) { route(); return; }        // same target → re-render
  if (replace) { history.replaceState(null, "", hash); route(); }
  else { location.hash = hash; }                          // fires hashchange → route()
}

// Back-compat helper used throughout the UI.
function go(view, path = "") { navigate(view, path); }

async function route() {
  const targetKey = location.hash || "#/";
  // A fresh click starts at the top; Back and Forward return to where you were.
  const wantTop = freshNavigation;
  freshNavigation = false;
  // Read the target BEFORE rendering. Swapping in the new view resets
  // scrollTop to 0 and fires a scroll event, which would otherwise overwrite
  // the very position we are about to restore.
  const want = wantTop ? 0 : (scrollMem.get(targetKey) || 0);
  restoringScroll = true;                    // suppress saves during the render

  const out = routeView();
  scrollKey = targetKey;
  try { await out; } catch (e) { /* view reports its own errors */ }
  restoreScroll(want);
  return out;
}

function routeView() {
  if (leaveVideo) { leaveVideo(); leaveVideo = null; }  // flush playback progress
  if (state.channel) closeChannel();   // navigating away dismisses the channel overlay
  state.selectMode = false; state.selected.clear();  // select mode is per-view
  state.page = 0;                                      // per-view default; paged routes set it below
  document.getElementById("sidebar").classList.remove("open");
  const raw = location.hash.replace(/^#\/?/, "");
  const idx = raw.indexOf("/");
  const type = idx === -1 ? raw : raw.slice(0, idx);
  const arg = idx === -1 ? "" : decodeURIComponent(raw.slice(idx + 1));
  switch (type) {
    case "video":   return renderVideo(parseInt(arg, 10));
    case "allvideos":
      state.page = Math.max(0, (parseInt(arg, 10) || 1) - 1);
      return renderAllVideos();
    case "vr":      return renderVR();
    case "settings": return renderSettings();
    case "duplicates": return renderDuplicates();
    case "new":     return renderNew();
    case "history": return renderHistory();
    case "favorites": return renderFavorites();
    case "tags":    return renderTags();
    case "untagged": return renderUntagged();
    case "tag":     return renderTagVideos(arg);
    case "channel": return renderChannelSetup();
    case "search":  state.searchQuery = arg; return renderSearch(arg);
    case "browse":
    case "":
    default:        state.view = "browse"; state.path = arg; return renderBrowse();
  }
}

/* -------------------------------------------------------------------- init */
const THEMES = [
  ["mocha", "Catppuccin Mocha"], ["macchiato", "Catppuccin Macchiato"],
  ["frappe", "Catppuccin Frappé"], ["latte", "Catppuccin Latte"],
  ["dark", "Plain Dark"], ["light", "Plain Light"],
  ["nord", "Nord"], ["rosepine", "Rosé Pine"], ["gruvbox", "Gruvbox"],
];

function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);
  prefSet("theme", theme);
  const sel = document.getElementById("theme-select");
  if (sel) sel.value = theme;
}

async function init() {
  // theme
  let theme;
  theme = prefGet("theme");
  try {
    const vm = prefGet("view");
    if (vm === "list" || vm === "grid") state.viewMode = vm;
  } catch (e) {}
  state.config = await api.get("/api/config").catch(() => ({}));
  applyTheme(theme || state.config.default_theme || "mocha");
  if (state.config.site_name) {
    document.getElementById("brand-name").textContent = state.config.site_name;
    document.getElementById("search").setAttribute("placeholder", `Search ${state.config.site_name}…`);
    document.title = state.config.site_name;
  }

  const tagWarnBtn = document.getElementById("tag-warn-btn");
  if (tagWarnBtn) tagWarnBtn.addEventListener("click", () => {
    settingsCat = "tags";
    navigate("settings");
  });
  document.getElementById("settings-btn").addEventListener("click", () => navigate("settings"));

  // Logout button (only visible when password protection is enabled).
  if (state.config.auth_enabled) {
    const logoutBtn = document.getElementById("logout-btn");
    logoutBtn.style.display = "";
    logoutBtn.addEventListener("click", async () => {
      try { await api.post("/api/logout"); } catch (e) {}
      location.href = "/login";
    });
  }

  // Autoplay preference (off by default; the toggle lives in Settings → General).
  state.autoplay = prefGet("autoplay") === "1";
  state.vrDebug = prefGet("vr-debug") === "1";

  // Restore persisted UI preferences.
  try {
    const s = JSON.parse(prefGet("sort") || "null");
    if (s && s.field) state.sort = { field: s.field, order: s.order === "desc" ? "desc" : "asc" };
  } catch (e) {}
  try {
    const ex = JSON.parse(prefGet("expanded") || "[]");
    if (Array.isArray(ex)) state.treeExpanded = new Set(ex);
  } catch (e) {}

  document.getElementById("menu-toggle").addEventListener("click", () =>
    document.getElementById("sidebar").classList.toggle("open"));

  document.querySelectorAll(".nav-item[data-view]").forEach((n) => {
    n.addEventListener("click", () => {
      const view = n.getAttribute("data-view");
      go(view, view === "browse" ? (n.getAttribute("data-path") || "") : "");
    });
  });
  document.getElementById("new-channel-item").addEventListener("click", () => navigate("channel"));

  const search = document.getElementById("search");
  search.addEventListener("input", (e) => {
    const q = e.target.value.trim();
    clearTimeout(searchTimer);
    if (!q) { if (state.view === "search") go("browse", ""); return; }
    searchTimer = setTimeout(() => navigate("search", q, true), 350);
  });

  await loadTree();
  await loadChannels();
  loadVRNav();

  // We restore scroll ourselves; the browser's attempt fights ours on reload.
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  const main = document.getElementById("main");
  if (main) {
    let scrollTimer;
    main.addEventListener("scroll", () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(rememberScroll, 120);
    }, { passive: true });
    // A wheel or touch during restoration means the user wants control back.
    const yieldScroll = () => { restoringScroll = false; };
    main.addEventListener("wheel", yieldScroll, { passive: true });
    main.addEventListener("touchstart", yieldScroll, { passive: true });
  }

  window.addEventListener("hashchange", route);
  refreshTagWarning();
  route(); // render the current hash (or the library root if none)
}

init();
