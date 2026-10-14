// Dataset details: overview, files, row preview, schema and the import panel.
import {api} from "../api.js?v=2";
import {badge, copyText, emptyState, errorPanel, h, mount, skeleton, tabs, toast} from "../dom.js?v=2";
import {FORMAT_NAMES, amountDone, buildHash, defaultTable, formatBytes, formatCount, objectScriptCall, sqlCall, timeAgo,
        usability} from "../state.js?v=2";

const ROWS = 20;
const TABS = [["overview", "Overview"], ["files", "Files"], ["preview", "Preview"], ["schema", "Schema"]];
const ALL = "*";

export function render(main, route, ctx = {}) {
  const id = route.id;
  const p = route.params;
  const state = {file: p.file || "", sheet: p.sheet || "", version: p.version || "",
                 tab: TABS.some(t => t[0] === p.tab) ? p.tab : "overview", offset: Math.max(0, Number(p.offset) || 0)};
  const controllers = new Set();
  let poll = null;
  let info = null;
  let sheets = null;                  // sheet names of the selected Excel file, once previewed
  let planCache = {key: "", promise: null};

  function signal() {
    const c = new AbortController();
    controllers.add(c);
    return c.signal;
  }

  function syncUrl() {
    history.replaceState(null, "", buildHash("dataset", {file: state.file, sheet: state.sheet, version: state.version,
      tab: state.tab === "overview" ? "" : state.tab, offset: state.offset || ""}, id));
  }

  mount(main, h("div", {class: "page"}, skeleton(2, "skeleton-card"), skeleton(6, "skeleton-card")));
  if (ctx.setTitle) ctx.setTitle(id);

  async function loadDetails() {
    try {
      info = await api.dataset(id, state.version, signal());
    } catch (e) {
      if (e.name === "AbortError") return;
      mount(main, h("div", {class: "page"}, backLink(), errorPanel(e, loadDetails,
        e.code === "not_found" ? "Dataset not found" : "Could not load the dataset"),
        e.code === "not_found" ? privateHint() : null));
      return;
    }
    const importable = importableFiles();
    if (!importable.some(f => f.name === state.file)) {
      state.file = importable.length ? importable[0].name : "";
      state.sheet = "";
      state.offset = 0;
    }
    syncUrl();
    page();
  }

  const importableFiles = () => (info.files || []).filter(f => f.importable);
  const currentFile = () => (info.files || []).find(f => f.name === state.file) || null;
  const isExcel = () => (currentFile() || {}).format === "excel";

  // ----------------------------------------------------------------- page
  let tabBody = h("div");
  let tabCtl = null;
  let importPanel = null;

  function page() {
    importPanel = importBox();
    tabCtl = tabs(TABS, state.tab, (key, panel) => {
      state.tab = key;
      tabBody = panel;
      syncUrl();
      ({overview, files, preview, schema})[key]();
    }, "Dataset views");
    mount(main, h("div", {class: "page"},
      backLink(),
      h("header", {class: "ds-head"},
        h("h1", {class: "ds-title", text: info.title || info.ref}),
        h("p", {class: "ds-ref mono", text: info.ref}),
        info.subtitle ? h("p", {class: "muted", text: info.subtitle}) : null,
        h("div", {class: "badges"},
          info.private ? badge("Private", "warn") : null,
          info.pinned ? badge("version " + info.version, "warn") : null,
          (info.tags || []).slice(0, 6).map(t => badge(t, "accent")),
          info.license ? badge("license: " + info.license) : null),
        h("div", {class: "ds-stats"},
          h("span", {}, h("strong", {text: formatBytes(info.totalBytes)})),
          h("span", {}, h("strong", {text: formatCount(info.votes)}), " votes"),
          h("span", {}, h("strong", {text: formatCount(info.downloads)}), " downloads"),
          h("span", {title: "Kaggle usability rating"}, "usability ", h("strong", {text: usability(info.usability)})),
          info.lastUpdated ? h("span", {text: "updated " + timeAgo(info.lastUpdated)}) : null,
          h("a", {href: info.url, target: "_blank", rel: "noopener noreferrer", text: "View on Kaggle"}),
          h("button", {class: "btn btn-sm only-narrow", type: "button", text: "Jump to import",
                       onclick: () => importPanel.el.scrollIntoView({behavior: "smooth"})}))),
      h("div", {class: "ds-layout"},
        h("section", {class: "panel", "aria-label": "Dataset contents"},
          picker(),
          tabCtl.list,
          tabCtl.panel),
        h("aside", {class: "ds-side"}, importPanel.el))));
    tabCtl.select(state.tab);
  }

  function backLink() {
    return h("a", {href: "#/explore", class: "muted", text: "\u2190 Back to Explore"});
  }

  function privateHint() {
    return h("p", {class: "muted", text: "Kaggle answers the same way for a dataset that does not exist and for a private one. " +
      "Private datasets need KAGGLE_API_TOKEN in the container; the token stays on the IRIS server."});
  }

  function fileLabel(f) {
    return f.name + "  (" + (FORMAT_NAMES[f.format] || f.format) + (f.size != null ? ", " + formatBytes(f.size) : "") + ")";
  }

  function picker() {
    const importable = importableFiles();
    const fileSelect = h("select", {id: "file", onchange: e => selectFile(e.target.value)},
      importable.map(f => h("option", {value: f.name, text: fileLabel(f), selected: f.name === state.file})));
    const versions = info.versions || [];
    const versionSelect = h("select", {id: "version", onchange: e => {
      state.version = e.target.value;
      state.offset = 0;
      planCache = {key: "", promise: null};
      syncUrl();
      mount(main, h("div", {class: "page"}, skeleton(2, "skeleton-card"), skeleton(6, "skeleton-card")));
      loadDetails();
    }},
      h("option", {value: "", text: "Latest" + (info.currentVersion ? " (version " + info.currentVersion + ")" : ""), selected: !state.version}),
      versions.filter(v => v.number !== info.currentVersion).map(v => h("option", {value: String(v.number),
        selected: String(v.number) === String(state.version),
        text: "Version " + v.number + (v.date ? ", " + String(v.date).slice(0, 10) : "")})));
    const sheetSelect = isExcel() && sheets && sheets.length > 1 ? h("div", {class: "field"},
      h("label", {for: "sheet", text: "Sheet"}),
      h("select", {id: "sheet", onchange: e => {
        state.sheet = e.target.value;
        state.offset = 0;
        syncUrl();
        importPanel.setFile(state.file, state.sheet);
        tabCtl.select(state.tab);
      }}, sheets.map(s => h("option", {value: s, text: s, selected: s === (state.sheet || sheets[0])})))) : null;
    return h("div", {class: "picker"},
      importable.length ? h("div", {class: "field grow"}, h("label", {for: "file", text: "File"}), fileSelect) :
        h("p", {class: "muted", text: "This dataset has no files that can be imported (CSV, TSV, Parquet, JSON, JSONL, Excel)."}),
      sheetSelect,
      versions.length > 1 ? h("div", {class: "field"}, h("label", {for: "version", text: "Version"}), versionSelect) : null);
  }

  function selectFile(name, tab) {
    state.file = name;
    state.sheet = "";
    state.offset = 0;
    sheets = null;
    if (tab) state.tab = tab;
    syncUrl();
    page();
  }

  // ------------------------------------------------------------- overview
  function overview() {
    const files = info.files || [];
    mount(tabBody, h("div", {class: "stack"},
      h("p", {class: "description", text: info.description || "This dataset has no description."}),
      h("dl", {class: "kv"},
        h("dt", {text: "Dataset id"}), h("dd", {class: "mono", text: info.ref}),
        h("dt", {text: "Owner"}), h("dd", {text: info.owner || "-"}),
        h("dt", {text: "Version"}), h("dd", {text: String(info.version || "-") + (info.pinned ? " (pinned)" : " (latest)")}),
        h("dt", {text: "License"}), h("dd", {text: info.license || "-"}),
        h("dt", {text: "Files"}), h("dd", {text: `${files.length}${info.filesTruncated ? "+" : ""}, ${info.importableFiles} can be imported`}),
        h("dt", {text: "Size"}), h("dd", {text: formatBytes(info.totalBytes)}))));
  }

  // ---------------------------------------------------------------- files
  function files() {
    const list = info.files || [];
    if (!list.length) { mount(tabBody, emptyState("No files", "Kaggle lists no files for this version.")); return; }
    mount(tabBody, h("div", {class: "stack"},
      info.filesTruncated ? h("div", {class: "notice notice-warn", text: `Only the first ${list.length} files are listed.`}) : null,
      h("div", {class: "table-wrap"}, h("table", {class: "data files"},
        h("thead", {}, h("tr", {}, ["File", "Type", "Size", ""].map(t => h("th", {scope: "col", text: t})))),
        h("tbody", {}, list.map(f => h("tr", {class: f.importable ? "" : "muted"},
          h("td", {class: "mono", text: f.name}),
          h("td", {text: f.importable ? FORMAT_NAMES[f.format] : "not importable"}),
          h("td", {class: "num", text: formatBytes(f.size)}),
          h("td", {}, f.importable ? h("button", {class: "btn btn-sm", type: "button", text: "Preview",
            "aria-label": "Preview " + f.name, onclick: () => selectFile(f.name, "preview")}) : null))))))));
  }

  // -------------------------------------------------------------- preview
  function preview() {
    if (!state.file) { mount(tabBody, emptyState("Nothing to preview", "This dataset has no files that can be imported.")); return; }
    mount(tabBody, h("div", {class: "row"}, h("span", {class: "spinner"}),
      h("span", {class: "muted", text: "Reading the first rows of " + state.file + "..."})));
    api.preview({id: info.ref, file: state.file, offset: state.offset, rows: ROWS, version: state.version, sheet: state.sheet},
                signal()).then(showRows).catch(e => {
      if (e.name === "AbortError") return;
      if (e.code === "preview_unavailable") {
        mount(tabBody, h("div", {class: "stack"}, h("div", {class: "notice notice-warn", text: e.message}),
          h("p", {class: "muted", text: "You can still import the file; its columns are known once it is read."})));
      } else {
        mount(tabBody, errorPanel(e, preview, "Could not load rows"));
      }
    });
  }

  function showRows(data) {
    if (data.sheets && !sheets) {
      sheets = data.sheets;
      if (sheets.length > 1) { const pick = main.querySelector(".picker"); if (pick) pick.replaceWith(picker()); }
    }
    if (!data.rows.length) { mount(tabBody, emptyState("No rows", "The file is empty or the offset is past its end.")); return; }
    const types = data.types || [];
    const head = h("tr", {}, h("th", {scope: "col", class: "num", text: "#"}),
      data.columns.map((c, i) => h("th", {scope: "col"}, c, " ", types[i] ? h("span", {class: "type", text: types[i].type}) : null)));
    const body = data.rows.map((row, r) => h("tr", {},
      h("td", {class: "num", text: String(data.offset + r)}),
      row.map(v => {
        const text = v === null ? "null" : typeof v === "object" ? JSON.stringify(v) : String(v);
        return h("td", {class: v === null ? "placeholder" : "", title: text, text});
      })));
    const total = data.total;
    const last = data.offset + data.rows.length;
    const notes = [];
    if (total == null) notes.push("read from the start of the file, so the row count is not known yet");
    if (data.encoding && data.encoding !== "utf-8") notes.push("read as " + data.encoding);
    if (data.truncated) notes.push("long values are shortened, hover to see more");
    mount(tabBody, h("div", {class: "stack"},
      h("div", {class: "table-wrap"}, h("table", {class: "data"}, h("thead", {}, head), h("tbody", {}, body))),
      h("div", {class: "pager"},
        h("span", {text: `Rows ${formatCount(data.offset)} to ${formatCount(last - 1)}` + (total != null ? ` of ${formatCount(total)}` : "") +
          (notes.length ? "; " + notes.join("; ") : "")}),
        h("div", {class: "row"},
          h("button", {class: "btn btn-sm", text: "Previous", disabled: data.offset === 0,
                       onclick: () => { state.offset = Math.max(0, data.offset - ROWS); syncUrl(); preview(); }}),
          h("button", {class: "btn btn-sm", text: "Next",
                       disabled: (total != null ? last >= total : data.rows.length < ROWS) || last >= 1000,
                       onclick: () => { state.offset = last; syncUrl(); preview(); }}))),
      last >= 1000 ? h("p", {class: "muted", text: "The preview shows the first 1,000 rows. Import the file to see the rest."}) : null));
  }

  // --------------------------------------------------------------- schema
  function schema() {
    if (!state.file) { mount(tabBody, emptyState("No file", "This dataset has no files that can be imported.")); return; }
    const body = h("div", {class: "stack"}, h("p", {class: "muted", text: "Working out the IRIS columns for " + state.file + "..."}));
    mount(tabBody, body);
    getPlan({...importPanel.values(), file: state.file, sheet: state.sheet,
             table: importPanel.values().file === state.file ? importPanel.values().table : ""}).then(plan => {
      const rows = (plan.columns || []).map(c => h("tr", {},
        h("td", {class: "mono", text: c.source}),
        h("td", {class: "mono", text: c.name}),
        h("td", {class: "type", text: c.type}),
        h("td", {class: "muted", text: c.note || ""})));
      mount(body,
        h("p", {class: "muted", text: `${state.file} \u2192 ${plan.table}`}),
        rows.length ? h("div", {class: "table-wrap"}, h("table", {class: "data plan-cols"},
          h("thead", {}, h("tr", {}, ["File column", "IRIS column", "IRIS type", "Note"].map(t => h("th", {scope: "col", text: t})))),
          h("tbody", {}, rows))) : emptyState("Columns unknown", "The column types are only known once the file is read."),
        (plan.warnings || []).map(w => h("p", {class: "muted", text: w})));
    }).catch(e => {
      if (e.name !== "AbortError") mount(body, errorPanel(e, schema, "Could not build the import plan"));
    });
  }

  // ---------------------------------------------------------------- plan
  function getPlan(values) {
    const body = {dataset: info.ref, version: state.version, ...values};
    const key = JSON.stringify(body);
    if (planCache.key !== key) planCache = {key, promise: api.plan(body, signal())};
    return planCache.promise;
  }

  // -------------------------------------------------------------- import
  function importBox() {
    const importable = importableFiles();
    const fileSelect = h("select", {id: "imp-file"},
      importable.map(f => h("option", {value: f.name, text: f.name, selected: f.name === state.file})),
      importable.length > 1 ? h("option", {value: ALL, text: `All tabular files (${importable.length})`}) : null);
    const tableLabel = h("label", {for: "imp-table", text: "Target table"});
    const table = h("input", {id: "imp-table", value: p.table || defaultTable(state.file), autocomplete: "off"});
    const tableHint = h("span", {class: "hint", text: ""});
    const limit = h("input", {id: "imp-limit", type: "number", min: "0", step: "1", value: "1000"});
    const mode = h("select", {id: "imp-mode"},
      [["replace", "Replace"], ["append", "Append"], ["fail", "Fail"]].map(([v, t]) => h("option", {value: v, text: t})));
    const sheet = h("input", {id: "imp-sheet", value: state.sheet, placeholder: "first sheet", autocomplete: "off"});
    const sheetField = h("div", {class: "field full"}, h("label", {for: "imp-sheet", text: "Sheet"}), sheet);
    const planBtn = h("button", {class: "btn", type: "button", text: "Review plan"});
    const startBtn = h("button", {class: "btn btn-primary", type: "button", text: "Start import", disabled: true});
    const out = h("div", {class: "stack", "aria-live": "polite"});
    const snippet = h("pre", {class: "snippet"});
    const sqlSnippet = h("pre", {class: "snippet"});
    const sqlCopy = h("button", {class: "btn btn-sm", type: "button", text: "Copy SQL", onclick: () => copyText(sqlSnippet.textContent)});
    const where = h("p", {class: "muted"});
    let tableEdited = Boolean(p.table);

    const all = () => fileSelect.value === ALL;
    const fileFormat = () => (importable.find(f => f.name === fileSelect.value) || {}).format;
    const values = () => ({file: fileSelect.value, table: table.value.trim(), limit: Math.max(0, Number(limit.value) || 0),
                           mode: mode.value, sheet: fileFormat() === "excel" ? sheet.value.trim() : ""});
    function refreshFields() {
      tableLabel.textContent = all() ? "Target schema" : "Target table";
      tableHint.textContent = all() ? "One table per file, named after the file." : "";
      sheetField.hidden = fileFormat() !== "excel";
      if (!tableEdited) table.value = all() ? "KG" : defaultTable(fileSelect.value);
    }
    function refreshSnippets() {
      const v = {dataset: info.ref, version: state.version, ...values()};
      snippet.textContent = objectScriptCall(v);
      const sql = sqlCall(v);
      sqlSnippet.textContent = sql || "There is no SQL function for all files at once; import them one by one with dc_kaggle2iris.load_dataset.";
      sqlCopy.hidden = !sql;
      where.textContent = (all() ? "All tabular files" : fileSelect.value || "-") + " \u00B7 " +
        (state.version ? "version " + state.version : "latest version");
    }
    function reset() {
      startBtn.disabled = true;
      mount(out);
      refreshSnippets();
    }
    table.addEventListener("input", () => { tableEdited = table.value.trim() !== ""; reset(); });
    for (const el of [limit, sheet]) el.addEventListener("input", reset);
    mode.addEventListener("change", reset);
    fileSelect.addEventListener("change", () => { refreshFields(); reset(); });

    planBtn.addEventListener("click", async () => {
      planBtn.disabled = true;
      mount(out, h("div", {class: "row"}, h("span", {class: "spinner"}), h("span", {class: "muted", text: "Checking the file and the target table..."})));
      try {
        if (all()) showFiles(await api.files({id: info.ref, version: state.version, schema: table.value.trim()}, signal()));
        else showPlan(await getPlan(values()));
      } catch (e) {
        if (e.name !== "AbortError") mount(out, errorPanel(e, null, "Plan failed"));
      } finally {
        planBtn.disabled = false;
      }
    });

    function showPlan(pl) {
      const refused = pl.action === "refused";
      startBtn.disabled = refused;
      const verb = {create: "creates", replace: "replaces", append: "appends to"}[pl.action];
      mount(out,
        h("div", {class: "notice " + (refused ? "notice-danger" : "notice-ok")},
          refused ? pl.reason : `This import ${verb} ${pl.table}` + (pl.limit ? ` with up to ${formatCount(pl.limit)} rows` : "") + "."),
        h("p", {class: "muted", text: `${(pl.columns || []).length || "Unknown number of"} columns, version ${pl.version}` +
          (pl.download && pl.download.bytes && !pl.limit ? `, about ${formatBytes(pl.download.bytes)} to download` : "")}),
        (pl.warnings || []).map(w => h("p", {class: "muted", text: w})),
        (pl.columns || []).length ? h("button", {class: "link-btn", type: "button", text: "Show the columns", onclick: () => {
          if (fileSelect.value !== state.file) selectFile(fileSelect.value, "schema");
          else tabCtl.select("schema");
        }}) : null);
    }

    function showFiles(r) {
      startBtn.disabled = !r.files.length;
      const total = r.files.reduce((n, f) => n + (f.size || 0), 0);
      mount(out,
        h("div", {class: "notice notice-ok"}, `This import creates ${r.files.length} table${r.files.length === 1 ? "" : "s"}` +
          (Number(limit.value) > 0 ? `, each with up to ${formatCount(Number(limit.value))} rows` : "") + "."),
        h("ul", {class: "file-list"}, r.files.map(f => h("li", {},
          h("span", {class: "mono", text: f.file}), " \u2192 ", h("span", {class: "mono", text: f.table}),
          h("span", {class: "muted", text: " (" + formatBytes(f.size) + ")"})))),
        h("p", {class: "muted", text: `Version ${r.version}, ${formatBytes(total)} of files. The import stops at the first file that fails; ` +
          "the tables imported before it stay."}));
    }

    startBtn.addEventListener("click", async () => {
      startBtn.disabled = true;
      planBtn.disabled = true;
      try {
        follow(await api.startJob({dataset: info.ref, version: state.version, ...values()}));
      } catch (e) {
        planBtn.disabled = false;
        mount(out, errorPanel(e, null, "The import could not start"));
      }
    });

    function follow(job) {
      const bar = h("div", {style: "width: 0%"});
      const progress = h("div", {class: "progress indeterminate", role: "progressbar", "aria-label": "Import progress"}, bar);
      const line = h("p", {class: "muted"});
      const cancel = h("button", {class: "btn btn-sm btn-danger", type: "button", text: "Cancel import", onclick: async () => {
        cancel.disabled = true;
        try { await api.cancelJob(job.id); line.textContent = "Cancel requested; the import stops before its next batch of rows."; }
        catch (e) { toast(e.message, "error"); }
      }});
      mount(out, h("div", {class: "stack"}, h("div", {class: "row"}, h("strong", {text: "Import #" + job.id}),
        h("span", {class: "spacer"}), cancel), progress, line));
      const phases = {queued: "Waiting to start", resolving: "Looking up the dataset", fetching: "Downloading from Kaggle",
                      inserting: "Writing rows to IRIS", finishing: "Finishing"};
      const tick = async () => {
        let j;
        try { j = await api.job(job.id, signal()); } catch (e) { if (e.name !== "AbortError") line.textContent = e.message; return; }
        const running = j.state === "queued" || j.state === "running";
        const many = j.file === ALL;
        const share = many ? (j.filesTotal ? (j.filesDone + (j.rowsTotal ? j.rowsDone / j.rowsTotal : 0)) / j.filesTotal : 0)
                           : (j.rowsTotal ? j.rowsDone / j.rowsTotal : 0);
        if (share > 0) {
          progress.className = "progress";
          bar.style.width = Math.round(100 * share) + "%";
          progress.setAttribute("aria-valuenow", String(Math.round(100 * share)));
        }
        if (running) {
          line.textContent = (many && j.currentFile ? `File ${j.filesDone + 1} of ${j.filesTotal}, ${j.currentFile}: ` : "") +
            (phases[j.phase || j.state] || j.state) + (j.rowsTotal ? ", " + amountDone(j) : "...") +
            (j.elapsed != null ? ` (${j.elapsed} s)` : "");
          return;
        }
        clearInterval(poll);
        poll = null;
        planBtn.disabled = false;
        cancel.remove();
        planCache = {key: "", promise: null};
        const tablesDone = j.result && j.result.tables ? j.result.tables : [];
        const links = list => list.map((t, i) => [i ? ", " : "", h("a", {href: buildHash("tables", {}, t.table), text: t.table})]);
        if (j.state === "ok") {
          bar.style.width = "100%";
          progress.className = "progress";
          if (many) {
            mount(out, h("div", {class: "notice notice-ok"},
              `Imported ${tablesDone.length} files, ${formatCount(j.result.rows)} rows in total.`),
              h("p", {class: "muted"}, "Tables: ", links(tablesDone), ". ", h("a", {href: "#/imports", text: "See all imports"}), "."));
            toast(`Imported ${tablesDone.length} files from ${info.ref}`, "ok");
          } else {
            mount(out, h("div", {class: "notice notice-ok"},
              `Imported ${formatCount(j.result.rows)} rows into ${j.result.table} in ${j.result.seconds} s.`),
              h("p", {class: "muted"}, `Version ${j.result.version}. `,
                h("a", {href: buildHash("tables", {}, j.result.table), text: "Open the table"}), " or ",
                h("a", {href: "#/imports", text: "see all imports"}), "."));
            toast(`Imported ${formatCount(j.result.rows)} rows into ${j.result.table}`, "ok");
          }
        } else {
          mount(out, h("div", {class: "notice " + (j.state === "cancelled" ? "notice-warn" : "notice-danger")},
            (j.state === "cancelled" ? "Import cancelled. " : j.state === "interrupted" ? "Import interrupted. " : "Import failed. ") + (j.error || "")),
            tablesDone.length ? h("p", {class: "muted"}, "Imported before it stopped: ", links(tablesDone), ".") : null);
        }
      };
      poll = setInterval(tick, 1500);
      tick();
    }

    function setFile(name, sheetName) {
      if (importable.some(f => f.name === name)) fileSelect.value = name;
      sheet.value = sheetName || "";
      refreshFields();
      reset();
    }

    refreshFields();
    refreshSnippets();
    const el = h("section", {class: "panel stack", "aria-labelledby": "imp-title"},
      h("div", {}, h("h2", {id: "imp-title", text: "Import into IRIS"}), where),
      importable.length ? [
        h("div", {class: "form-grid"},
          h("div", {class: "field full"}, h("label", {for: "imp-file", text: "File"}), fileSelect),
          h("div", {class: "field full"}, tableLabel, table, tableHint),
          sheetField,
          h("div", {class: "field"}, h("label", {for: "imp-limit", text: "Row limit"}), limit,
            h("span", {class: "hint", text: "0 = all rows"})),
          h("div", {class: "field"}, h("label", {for: "imp-mode", text: "If the table exists"}), mode)),
        h("div", {class: "row"}, planBtn, startBtn),
        out,
        h("details", {}, h("summary", {class: "muted", text: "Same import in ObjectScript or SQL"}),
          h("div", {class: "stack", style: "margin-top: 8px"},
            snippet, h("button", {class: "btn btn-sm", type: "button", text: "Copy ObjectScript", onclick: () => copyText(snippet.textContent)}),
            sqlSnippet, sqlCopy))]
        : h("p", {class: "muted", text: "Nothing to import: no CSV, TSV, Parquet, JSON, JSONL or Excel files."}));
    return {el, values, reset, setFile};
  }

  loadDetails();
  return () => {
    for (const c of controllers) c.abort();
    if (poll) clearInterval(poll);
  };
}
