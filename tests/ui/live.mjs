// Runs the real view modules against the running server with a minimal fake DOM.
// No imports are started: "Start import" is never clicked.
// Usage: node tests/ui/live.mjs [http://localhost:52773]   (or set IRIS_WEB_PORT)
import {all, audit, byClass, byId, byText, find, install, waitFor} from "./fakedom.mjs";

const BASE = process.argv[2] || `http://localhost:${process.env.IRIS_WEB_PORT || 52773}`;
const main = install(BASE);
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (ok || !detail ? "" : "  -> " + detail));
};

const {VERSION} = await import("../../web/js/state.js");
// the same URL as the views use, so this is the same module instance
const api = await import(`../../web/js/api.js?v=${VERSION}`);
const logins = [];
api.onLogin(async message => {               // what the sign-in dialog does
  logins.push(message);
  api.saveLogin("_SYSTEM", logins.length === 1 ? "wrong" : "SYS");
});
const explore = await import(`../../web/js/views/explore.js?v=${VERSION}`);
const dataset = await import(`../../web/js/views/dataset.js?v=${VERSION}`);
const imports = await import(`../../web/js/views/imports.js?v=${VERSION}`);
const tables = await import(`../../web/js/views/tables.js?v=${VERSION}`);
const a11y = name => { const problems = audit(main); check("accessibility: " + name, !problems.length, problems.slice(0, 3).join("; ")); };
const nav = {navigate: () => {}};
const text = el => el ? el.textContent : "";

// ------------------------------------------------------------------ explore
let stop = explore.render(main, {view: "explore", params: {}}, nav);
await waitFor(() => byClass(main, "card").length || byClass(main, "error-panel").length, "popular datasets");
check("sign-in asked, wrong password rejected, then accepted",
      logins.length === 2 && logins[1] === "Wrong username or password.", JSON.stringify(logins));
check("hottest datasets listed", byClass(main, "card").length > 0 && text(main).includes("Hottest datasets"));
a11y("explore");
stop();

stop = explore.render(main, {view: "explore", params: {q: "iris"}}, nav);
await waitFor(() => byClass(main, "card").length, "iris results");
const irisCard = find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/dataset/uciml/iris");
check("search finds uciml/iris with a dataset link", Boolean(irisCard));
check("result card shows votes, size and badges", irisCard && /votes/.test(text(irisCard)) && /KB/.test(text(irisCard)) &&
      byClass(irisCard, "badge").length > 0);
byText(main, "button", "Most votes").click();
await waitFor(() => location.hash.includes("sort=votes"), "sort in URL");
check("sort button updates the URL and pressed state",
      byText(main, "button", "Most votes").getAttribute("aria-pressed") === "true");
const fileType = byId(main, "type");
fileType.value = "csv";
fileType.fire("change");
await waitFor(() => location.hash.includes("type=csv"), "file type in URL");
check("file type filter goes into the URL", true);
stop();

stop = explore.render(main, {view: "explore", params: {q: "zzqx-no-such-dataset-8841"}}, nav);
await waitFor(() => text(main).includes("No datasets found") || byClass(main, "card").length, "empty result");
check("empty search shows an empty state with a clear button",
      text(main).includes("No datasets found") && Boolean(byText(main, "button", "Clear search and filters")));
stop();

// ------------------------------------------------------------------ dataset
stop = dataset.render(main, {view: "dataset", id: "uciml/iris", params: {}}, nav);
await waitFor(() => byClass(main, "ds-title").length || byClass(main, "error-panel").length, "iris details");
check("dataset header", text(byClass(main, "ds-title")[0]) === "Iris Species" && text(byClass(main, "ds-ref")[0]) === "uciml/iris");
check("first importable file picked", byId(main, "file").value === "Iris.csv" && byId(main, "imp-file").value === "Iris.csv");
check("overview counts the files", text(main).includes("1 can be imported"));
check("Kaggle link opens in a new tab", Boolean(find(main, e => e.tagName === "A" &&
      e.getAttribute("href") === "https://www.kaggle.com/datasets/uciml/iris" && e.getAttribute("target") === "_blank")));
a11y("dataset");
const tablist = find(main, e => e.getAttribute("role") === "tablist");
tablist.fire("keydown", {key: "ArrowRight"});
check("arrow key moves to the next tab", location.hash.includes("tab=files") &&
      byText(main, "button", "Files").getAttribute("tabindex") === "0" && byText(main, "button", "Overview").getAttribute("tabindex") === "-1");
check("files tab lists the file with its size", /Iris\.csv\s*CSV\s*5 KB/.test(text(main)));
tablist.fire("keydown", {key: "Home"});
check("Home key returns to the first tab", byText(main, "button", "Overview").getAttribute("aria-selected") === "true");

byText(main, "button", "Preview").click();
await waitFor(() => find(main, e => e.tagName === "TABLE") || byClass(main, "error-panel").length, "preview rows");
const firstPage = all(find(main, e => e.tagName === "TBODY"), e => e.tagName === "TR").length;
check("preview shows 20 rows with a pager", firstPage === 20 && /Rows 0 to 19 of 150/.test(text(main)), text(byClass(main, "pager")[0]));
check("preview shows column types", /SepalLengthCm\s*number/.test(text(main)));
byText(main, "button", "Next").click();
await waitFor(() => /Rows 20 to 39/.test(text(main)), "next page");
check("next page and URL offset", location.hash.includes("offset=20"));

byText(main, "button", "Schema").click();
await waitFor(() => find(main, e => e.tagName === "TABLE" && (" " + e.className).includes("plan-cols")) ||
                    byClass(main, "error-panel").length, "schema plan");
check("schema shows IRIS columns from the plan", /Id\s*Id_\s*BIGINT/.test(text(main)) && text(main).includes("Iris.csv → KG.Iris"));

check("ObjectScript snippet", text(main).includes('do ##class(dc.kaggle2iris.Loader).Load("uciml/iris","Iris.csv","KG.Iris",1000)'));
check("SQL snippet", text(main).includes("SELECT dc_kaggle2iris.load_dataset('uciml/iris', 'Iris.csv', 'KG.Iris', 1000)"));
const start = byText(main, "button", "Start import");
check("Start import is disabled before a plan", start.disabled === true);
byText(main, "button", "Review plan").click();
await waitFor(() => byClass(main, "notice-ok").length || byClass(main, "notice-danger").length ||
                    byClass(main, "error-panel").length, "plan");
check("plan says what the import does", /This import (creates|replaces) KG\.Iris with up to 1K rows/.test(text(main)),
      text(byClass(main, "notice")[0]));
check("Start import is enabled after a good plan", start.disabled === false);

const table = byId(main, "imp-table");
table.value = "dc_kaggle2iris.ImportLog";
table.fire("input");
const mode = byId(main, "imp-mode");
mode.value = "append";
mode.fire("change");
byText(main, "button", "Review plan").click();
await waitFor(() => byClass(main, "notice-danger").length || byClass(main, "error-panel").length, "refused plan");
check("plan for a foreign table is refused and Start stays disabled",
      /not created by kaggle2iris/.test(text(main)) && start.disabled === true);

const version = byId(main, "version");
version.value = "1";
version.fire("change");
await waitFor(() => location.hash.includes("version=1") && byClass(main, "ds-title").length, "version 1");
check("an older version is pinned", text(main).includes("version 1") && text(main).includes('"replace","1")'),
      text(byClass(main, "snippet")[0]));
stop();

stop = dataset.render(main, {view: "dataset", id: "olistbr/brazilian-ecommerce", params: {tab: "files"}}, nav);
await waitFor(() => find(main, e => e.tagName === "TABLE") || byClass(main, "error-panel").length, "olist files");
check("files tab lists all files", all(find(main, e => e.tagName === "TBODY"), e => e.tagName === "TR").length >= 9);
const impFile = byId(main, "imp-file");
check("import panel offers all tabular files", all(impFile, e => e.tagName === "OPTION").some(o => o.value === "*"));
impFile.value = "*";
impFile.fire("change");
byText(main, "button", "Review plan").click();
await waitFor(() => /This import creates \d+ tables/.test(text(main)) || byClass(main, "error-panel").length, "all-files plan");
check("all-files plan lists file to table", text(main).includes("KG.olist_orders_dataset"));
stop();

stop = dataset.render(main, {view: "dataset", id: "no-such-user/no-such-dataset", params: {}}, nav);
await waitFor(() => byClass(main, "error-panel").length, "not found");
check("missing dataset shows an error with Try again and the private hint",
      text(main).includes("Dataset not found") && Boolean(byText(main, "button", "Try again")) && text(main).includes("KAGGLE_API_TOKEN"));
stop();

// ------------------------------------------------------------------- tables
// Uses whatever loader tables and history exist; parts with nothing to look at are skipped.
const skip = name => console.log("SKIP  " + name);
const known = (await api.api.tables()).items || [];
const sample = known.find(t => t.rows > 50 && t.dataset) || known.find(t => t.rows > 0) || null;

stop = tables.render(main, {view: "tables", id: null, params: {}}, nav);
await waitFor(() => find(main, e => e.tagName === "TABLE") || byClass(main, "empty-state").length || byClass(main, "error-panel").length, "tables list");
a11y("tables list");
if (!known.length) {
  check("no tables yet shows an empty state", text(main).includes("No tables yet"));
} else {
  check("tables list links to every table", known.every(t => find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/tables/" + t.table)));
  const filter = byId(main, "tbl-filter");
  filter.value = "zzqx-no-table";
  filter.fire("input");
  check("table filter", text(main).includes("No matching tables"));
}
stop();

if (sample) {
  stop = tables.render(main, {view: "tables", id: sample.table, params: {}}, nav);
  const pageRows = Math.min(50, sample.rows);
  await waitFor(() => new RegExp(`Rows 0 to ${pageRows - 1} of`).test(text(main)) || byClass(main, "error-panel").length, "table rows");
  check("table detail pages rows by 50", all(find(main, e => e.tagName === "TBODY"), e => e.tagName === "TR").length === pageRows);
  if (sample.rows > 50) {
    byText(main, "button", "Next").click();
    await waitFor(() => /Rows 50 to/.test(text(main)), "table page 2");
    check("next page of a table", location.hash.includes("offset=50"));
  } else skip("next page of a table (fewer than 51 rows)");
  const reimport = byText(main, "a", "Re-import");
  if (sample.dataset) {
    const want = "#/dataset/" + sample.dataset + "?" + new URLSearchParams(Object.entries(
      {file: sample.file || "", sheet: sample.sheet || "", table: sample.table}).filter(([, v]) => v)).toString();
    check("re-import opens the dataset with its settings filled in", reimport && reimport.getAttribute("href") === want,
          reimport && reimport.getAttribute("href") + " vs " + want);
  }
  const ttabs = find(main, e => e.getAttribute("role") === "tablist");
  ttabs.fire("keydown", {key: "End"});
  check("End key opens the last tab (imports)", byText(main, "button", "Imports").getAttribute("aria-selected") === "true");
  ttabs.fire("keydown", {key: "ArrowLeft"});
  check("columns tab lists IRIS types", text(main).includes("IRIS type") && /(BIGINT|VARCHAR|DOUBLE)/.test(text(main)));
  a11y("table detail");
  stop();
} else skip("table detail (no tables yet)");

stop = tables.render(main, {view: "tables", id: "dc_kaggle2iris.ImportLog", params: {}}, nav);
await waitFor(() => byClass(main, "error-panel").length, "foreign table");
check("a table not created by kaggle2iris is not shown", text(main).includes("Table not found"));
stop();

stop = dataset.render(main, {view: "dataset", id: "uciml/iris", params: {table: "Demo.Reviews"}}, nav);
await waitFor(() => byId(main, "imp-table") || byClass(main, "error-panel").length, "prefilled import");
check("import panel prefilled from the URL", byId(main, "imp-table") && byId(main, "imp-table").value === "Demo.Reviews");
stop();

// ------------------------------------------------------------------ imports
const jobs = (await api.api.jobs(20)).items || [];
const historyRows = await api.api.history();
stop = imports.render(main, {view: "imports", id: null, params: {}}, nav);
await waitFor(() => byClass(main, "job").length || byClass(main, "empty-state").length || byClass(main, "error-panel").length, "imports view");
await waitFor(() => find(main, e => e.tagName === "TABLE") || text(main).includes("Nothing imported yet"), "import history");
if (jobs.length) check("background imports are listed", byClass(main, "job").length === Math.min(20, jobs.length));
else check("no background imports yet shows an empty state", text(main).includes("No background imports yet"));
if (historyRows.length) {
  check("history lists earlier imports", text(main).includes(historyRows[0].dataset || historyRows[0].table || ""));
  const wanted = historyRows.some(r => r.status === "ERROR") ? "ERROR" : "OK";
  const status = byId(main, "hist-status");
  status.value = wanted;
  status.fire("change");
  const statuses = byClass(find(main, e => e.tagName === "TBODY"), "badge").map(b => b.textContent);
  check("history status filter", statuses.length > 0 && statuses.every(t => t === wanted), statuses.join(","));
} else {
  check("empty history shows an empty state", text(main).includes("Nothing imported yet"));
}
a11y("imports");
stop();

const failed = results.filter(r => !r).length;
console.log(`UI ${failed ? "FAILED: " + failed + " of " + results.length : "PASSED: " + results.length + " checks"}`);
process.exit(failed ? 1 : 0);
