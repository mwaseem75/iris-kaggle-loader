// Imports: background jobs (with progress and cancel) and the import history.
import {api} from "../api.js?v=2";
import {badge, emptyState, errorPanel, h, mount, skeleton, toast} from "../dom.js?v=2";
import {amountDone, buildHash, formatCount, serverTime} from "../state.js?v=2";

const STATE_BADGE = {queued: "accent", running: "accent", ok: "ok", error: "danger", cancelled: "warn", interrupted: "warn"};
const PHASES = {resolving: "Looking up the dataset", fetching: "Downloading from Kaggle", inserting: "Writing rows",
                finishing: "Finishing"};

export function render(main, route, ctx = {}) {
  if (ctx.setTitle) ctx.setTitle("Imports");
  let controller = null;
  let timer = null;
  let filter = {status: "", text: ""};
  let history = [];

  const jobsBox = h("div", {"aria-live": "polite"}, skeleton(2));
  const historyBox = h("div", {}, skeleton(4));
  const statusSelect = h("select", {id: "hist-status", onchange: e => { filter.status = e.target.value; showHistory(); }},
    [["", "All"], ["OK", "Succeeded"], ["ERROR", "Failed"]].map(([v, t]) => h("option", {value: v, text: t})));
  const textInput = h("input", {id: "hist-text", type: "search", placeholder: "Dataset, file or table", autocomplete: "off",
    oninput: e => { filter.text = e.target.value.trim().toLowerCase(); showHistory(); }});

  mount(main, h("div", {class: "page"},
    h("div", {class: "page-head"}, h("div", {}, h("h1", {text: "Imports"}),
      h("p", {text: "Background imports with their progress, and every import into IRIS so far."}))),
    h("section", {class: "panel", "aria-labelledby": "jobs-title"},
      h("div", {class: "panel-head"}, h("h2", {id: "jobs-title", text: "Background imports"})), jobsBox),
    h("section", {class: "panel", "aria-labelledby": "hist-title"},
      h("div", {class: "panel-head"}, h("h2", {id: "hist-title", text: "Import history"}),
        h("div", {class: "toolbar"},
          h("div", {class: "field"}, h("label", {for: "hist-status", text: "Status"}), statusSelect),
          h("div", {class: "field"}, h("label", {for: "hist-text", text: "Filter"}), textInput))),
      historyBox)));

  async function load() {
    clearTimeout(timer);
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    let jobs;
    try {
      [jobs, history] = await Promise.all([api.jobs(20, signal), api.history(signal)]);
    } catch (e) {
      if (e.name === "AbortError") return;
      mount(jobsBox, errorPanel(e, load, "Could not load the imports"));
      mount(historyBox);
      return;
    }
    showJobs(jobs.items || []);
    showHistory();
    if ((jobs.items || []).some(j => j.state === "queued" || j.state === "running")) timer = setTimeout(load, 2000);
  }

  function showJobs(items) {
    if (!items.length) {
      mount(jobsBox, emptyState("No background imports yet", "Start one from a dataset page.",
        h("a", {class: "btn", href: "#/explore", text: "Explore datasets"})));
      return;
    }
    mount(jobsBox, h("ul", {class: "job-list"}, items.map(job)));
  }

  function job(j) {
    const running = j.state === "queued" || j.state === "running";
    const many = j.file === "*";
    const share = many ? (j.filesTotal ? (j.filesDone + (j.rowsTotal ? j.rowsDone / j.rowsTotal : 0)) / j.filesTotal : 0)
                       : (j.rowsTotal ? j.rowsDone / j.rowsTotal : 0);
    const pct = share > 0 ? Math.round(100 * share) : null;
    const tablesDone = j.result && j.result.tables ? j.result.tables : null;
    const cancel = running ? h("button", {class: "btn btn-sm btn-danger", type: "button", text: "Cancel",
      "aria-label": `Cancel import ${j.id}`, disabled: j.cancelRequested,
      onclick: async () => {
        cancel.disabled = true;
        try { await api.cancelJob(j.id); toast(`Cancel requested for import ${j.id}`); load(); }
        catch (e) { cancel.disabled = false; toast(e.message, "error"); }
      }}) : null;
    return h("li", {class: "job"},
      h("div", {class: "job-head"},
        badge(j.state, STATE_BADGE[j.state] || ""),
        h("strong", {text: "#" + j.id}),
        h("a", {href: buildHash("dataset", many ? {} : {file: j.file, sheet: j.sheet || ""}, j.dataset), class: "mono", text: j.dataset}),
        h("span", {class: "muted", text: many ? "all files" : j.file}),
        h("span", {class: "muted", text: "\u2192"}),
        j.state === "ok" && !many ? h("a", {href: buildHash("tables", {}, j.table), class: "mono", text: j.table})
                                  : h("span", {class: "mono", text: many ? "schema " + j.table : j.table}),
        h("span", {class: "spacer"}),
        h("span", {class: "muted", title: "server time", text: serverTime(j.createdAt)}),
        cancel),
      running ? h("div", {class: "stack"},
        h("div", {class: "progress" + (pct === null ? " indeterminate" : ""), role: "progressbar",
                  "aria-label": `Import ${j.id} progress`, "aria-valuenow": pct === null ? null : String(pct),
                  "aria-valuemin": "0", "aria-valuemax": "100"},
          h("div", {style: `width: ${pct === null ? 0 : pct}%`})),
        h("p", {class: "muted", text: (j.cancelRequested ? "Cancel requested. " : "") +
          (many && j.currentFile ? `File ${j.filesDone + 1} of ${j.filesTotal}, ${j.currentFile}: ` : "") + (PHASES[j.phase] || "Waiting to start") +
          (j.rowsTotal ? ": " + amountDone(j) : "") +
          (j.elapsed != null ? ` (${j.elapsed} s)` : "")})) : null,
      !running ? h("details", {},
        h("summary", {class: "muted", text: j.state === "ok"
          ? `${formatCount(j.result && j.result.rows)} rows in ${j.elapsed} s` : "Details"}),
        j.state === "ok" && j.result ? h("dl", {class: "kv"},
          tablesDone ? [h("dt", {text: "Tables"}), h("dd", {}, tablesDone.map((t, i) => [i ? ", " : "",
            h("a", {class: "mono", href: buildHash("tables", {}, t.table), text: t.table}), ` (${formatCount(t.rows)})`]))]
            : [h("dt", {text: "Columns"}), h("dd", {class: "mono", text: Object.entries(j.result.columns || {}).map(([c, t]) => c + " " + t).join(", ")})],
          h("dt", {text: "Version"}), h("dd", {text: String(j.result.version || "-")}),
          j.result.encoding && j.result.encoding !== "utf-8" ? [h("dt", {text: "Encoding"}), h("dd", {text: j.result.encoding})] : null,
          h("dt", {text: "Started by"}), h("dd", {text: j.user || "-"}))
          : [h("p", {class: "job-error", text: j.error || "No details."}),
             tablesDone && tablesDone.length ? h("p", {class: "muted", text: "Imported before it stopped: " + tablesDone.map(t => t.table).join(", ")}) : null]) : null);
  }

  function showHistory() {
    if (!history.length) {
      mount(historyBox, emptyState("Nothing imported yet", "Imports from this page, REST, SQL and ObjectScript all appear here."));
      return;
    }
    const rows = history.filter(r => (!filter.status || r.status === filter.status) &&
      (!filter.text || (r.dataset + " " + r.file + " " + r.table).toLowerCase().includes(filter.text)));
    if (!rows.length) {
      mount(historyBox, emptyState("No matching imports", "Change the status or the filter."));
      return;
    }
    mount(historyBox, h("div", {class: "table-wrap"}, h("table", {class: "data wrap"},
      h("thead", {}, h("tr", {}, ["When", "Dataset", "File", "Table", "Rows", "Time", "Status", "Message"]
        .map(t => h("th", {scope: "col", text: t})))),
      h("tbody", {}, rows.map(r => h("tr", {},
        h("td", {class: "nowrap", title: "server time", text: serverTime(r.createdAt)}),
        h("td", {}, r.dataset ? h("a", {class: "mono", href: buildHash("dataset", {file: r.file || "", sheet: r.sheet || ""}, r.dataset),
                                       text: r.dataset}) : "-"),
        h("td", {class: "mono", text: [r.file, r.sheet].filter(Boolean).join(" / ") || "-"}),
        h("td", {}, r.status === "OK" && r.table ? h("a", {class: "mono", href: buildHash("tables", {}, r.table), text: r.table})
                                                 : h("span", {class: "mono", text: r.table || "-"})),
        h("td", {class: "num", text: r.rows === "" || r.rows === null ? "-" : formatCount(r.rows)}),
        h("td", {class: "num", text: r.seconds === "" || r.seconds === null ? "-" : r.seconds + " s"}),
        h("td", {}, badge(r.status, r.status === "OK" ? "ok" : "danger")),
        h("td", {class: "message", text: r.message || ""})))))));
  }

  load();
  return () => { clearTimeout(timer); if (controller) controller.abort(); };
}
