// Pure helpers without browser APIs: routes, snippets and formatting.

export const VERSION = "2";

// "#/dataset/uciml/iris?tab=preview" -> {view: "dataset", id: "uciml/iris", params: {tab: "preview"}}
// "#/tables/KG.Iris" -> {view: "tables", id: "KG.Iris", ...}; "#/imports" -> {view: "imports", id: null, ...}
export function parseHash(hash) {
  const text = String(hash || "").replace(/^#\/?/, "");
  const [path, query = ""] = text.split("?");
  const params = Object.fromEntries(new URLSearchParams(query));
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "dataset" && parts.length >= 2) {
    return {view: "dataset", id: parts.slice(1, 3).join("/"), params};
  }
  if (parts[0] === "tables") return {view: "tables", id: parts[1] || null, params};
  if (parts[0] === "imports") return {view: "imports", id: null, params};
  return {view: "explore", id: null, params};
}

export function buildHash(view, params = {}, id = null) {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") query.set(k, v);
  }
  const path = id && (view === "dataset" || view === "tables") ? view + "/" + id.split("/").map(encodeURIComponent).join("/") : view;
  const qs = query.toString();
  return "#/" + path + (qs ? "?" + qs : "");
}

// The equivalent calls for an import, without trailing default arguments.
function trimDefaults(args, defaults, keep) {
  const out = args.slice();
  while (out.length > keep && out[out.length - 1] === defaults[out.length - 1]) out.pop();
  return out;
}

// File "*" means every tabular file; then table is the schema and Loader.LoadAll is used.
function loadArgs({dataset, file = "", table = "", limit = 0, mode = "replace", version = "", sheet = ""}) {
  if (file === "*") {
    return trimDefaults([dataset, table || "", Number(limit) || 0, mode || "replace", String(version || "")],
                        [null, "", 0, "replace", ""], 1);
  }
  return trimDefaults([dataset, file || "", table || "", Number(limit) || 0, mode || "replace", String(version || ""), sheet || ""],
                      [null, null, "", 0, "replace", "", ""], 2);
}

export function objectScriptCall(v) {
  const s = x => '"' + String(x ?? "").replace(/"/g, '""') + '"';
  const method = v.file === "*" ? "LoadAll" : "Load";
  return `do ##class(dc.kaggle2iris.Loader).${method}(` + loadArgs(v).map(a => typeof a === "number" ? String(a) : s(a)).join(",") + ")";
}

// There is no SQL function for all files at once: null.
export function sqlCall(v) {
  if (v.file === "*") return null;
  const s = x => (x === "" || x === null || x === undefined) ? "NULL" : "'" + String(x).replace(/'/g, "''") + "'";
  return "SELECT dc_kaggle2iris.load_dataset(" + loadArgs(v).map(a => typeof a === "number" ? String(a) : s(a)).join(", ") + ")";
}

export function formatCount(n) {
  if (n === null || n === undefined || n === "") return "-";
  const v = Number(n);
  if (!isFinite(v)) return "-";
  if (Math.abs(v) < 1000) return String(v);
  const units = [["B", 1e9], ["M", 1e6], ["K", 1e3]];
  for (const [u, size] of units) {
    if (Math.abs(v) >= size) {
      const x = v / size;
      return (x >= 100 ? Math.round(x) : Math.round(x * 10) / 10) + u;
    }
  }
  return String(v);
}

export function formatBytes(n) {
  if (n === null || n === undefined || n === "") return "-";
  let v = Number(n);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + " " + units[i];
}

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  const steps = [[60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"], [Infinity, "year"]];
  let v = s;
  for (const [size, unit] of steps) {
    if (v < size) return v <= 1 && unit === "second" ? "just now" : v + " " + unit + (v === 1 ? "" : "s") + " ago";
    v = Math.floor(v / size);
  }
  return "";
}

// "12 MB of 40 MB" while a job downloads (rowsDone/rowsTotal are bytes then), "300 of 1K rows" after.
export function amountDone(job) {
  if (!job || !job.rowsTotal) return "";
  return job.phase === "fetching" ? `${formatBytes(job.rowsDone)} of ${formatBytes(job.rowsTotal)}`
                                  : `${formatCount(job.rowsDone)} of ${formatCount(job.rowsTotal)} rows`;
}

// The table a file goes to by default: "KG.<file name without extension>" (the server cleans it further).
export function defaultTable(file) {
  const base = String(file || "").split("/").pop().replace(/\.[^.]*$/, "") || "data";
  return "KG." + base.replace(/[^A-Za-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
}

// Usability is 0..1 on Kaggle; shown out of 10 like on the site.
export const usability = u => (u === null || u === undefined || u === "" ? "-" : (Math.round(Number(u) * 100) / 10).toFixed(1));

export const SORTS = [["hottest", "Hottest"], ["votes", "Most votes"], ["updated", "Recently updated"], ["published", "Newest"]];

export const FILE_TYPES = [["csv", "CSV"], ["json", "JSON"], ["parquet", "Parquet"]];

export const LICENSES = [["cc", "Creative Commons"], ["gpl", "GPL"], ["odb", "Open Database"], ["other", "Other"]];

// Total dataset size filters, in bytes
export const SIZES = [["small", "Under 1 MB", "", 1048576], ["medium", "1 MB to 100 MB", 1048576, 104857600],
                      ["large", "100 MB to 1 GB", 104857600, 1073741824], ["huge", "Over 1 GB", 1073741824, ""]];

export const FORMAT_NAMES = {csv: "CSV", tsv: "TSV", parquet: "Parquet", json: "JSON", jsonl: "JSONL", excel: "Excel"};

// ------------------------------------------------------------------ theme
export const THEME_KEY = "kaggle2iris.theme";
export const themeFor = saved => (saved === "light" ? "light" : "dark");

// WCAG contrast of two colors: "#rrggbb" or "rgba(r, g, b, a)" (blended over `under`).
export function parseColor(text, under = [0, 0, 0]) {
  const t = String(text).trim();
  let m = t.match(/^#([0-9a-f]{6})$/i);
  if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16));
  m = t.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const [r, g, b, a = 1] = m[1].split(",").map(Number);
    return [r, g, b].map((v, i) => Math.round(v * a + under[i] * (1 - a)));
  }
  throw new Error("unsupported color " + t);
}

function luminance([r, g, b]) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// Server times ("2026-10-12 22:41:22") are shown as they are, without the seconds.
export const serverTime = t => (t ? String(t).slice(0, 16) : "");
