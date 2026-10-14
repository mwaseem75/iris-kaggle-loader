// Offline test of import monitoring with a fake API: nothing is imported, no server needed.
// Usage: node tests/ui/mocked.mjs
import {audit, byClass, byId, byText, find, install, waitFor} from "./fakedom.mjs";

const main = install("");
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (ok || !detail ? "" : "  -> " + detail));
};
const text = el => el ? el.textContent : "";
const json = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json"}});

// ------------------------------------------------------------- fake API
const calls = [];
let job7 = {id: "7", state: "running", phase: "inserting", rowsDone: 1000, rowsTotal: 4000, dataset: "x/y", file: "data.csv",
            sheet: null, table: "KG.data", mode: "replace", version: null, filesDone: 0, filesTotal: 1, currentFile: "data.csv",
            createdAt: "2026-10-13 10:00:00", elapsed: 5, user: "_SYSTEM", cancelRequested: false, result: null, error: null};
const job8 = {id: "8", state: "ok", phase: null, rowsDone: 30, rowsTotal: 30, dataset: "x/y", file: "*", table: "KG",
              mode: "replace", filesDone: 2, filesTotal: 2, createdAt: "2026-10-13 09:00:00", elapsed: 4, user: "_SYSTEM",
              cancelRequested: false, error: null,
              result: {rows: 30, version: 2, tables: [{table: "KG.a", rows: 10}, {table: "KG.b", rows: 20}]}};
const job9 = [
  {id: "9", state: "running", phase: "fetching", rowsDone: 1048576, rowsTotal: 4194304, file: "data.csv", elapsed: 1},
  {id: "9", state: "running", phase: "inserting", rowsDone: 2000, rowsTotal: 4000, file: "data.csv", elapsed: 2},
  {id: "9", state: "ok", phase: null, rowsDone: 4000, rowsTotal: 4000, file: "data.csv", elapsed: 3,
   result: {rows: 4000, table: "KG.data", seconds: 3.2, version: 2}}];
let job9Step = 0;
globalThis.fetch = async (url, opts = {}) => {
  const path = url.replace("/api/kaggle2iris", "");
  calls.push((opts.method || "GET") + " " + path);
  if (path.startsWith("/jobs?")) return json({items: [job7, job8], count: 2});
  if (path === "/history") return json([]);
  if (path === "/jobs/7/cancel") {
    job7 = {...job7, cancelRequested: true};
    setTimeout(() => { job7 = {...job7, state: "cancelled", phase: null, error: "Import cancelled. The rows of this import were rolled back."}; }, 300);
    return json(job7);
  }
  if (path.startsWith("/dataset?")) {
    return json({ref: "x/y", title: "Fake data", subtitle: "Two small files", owner: "x", totalBytes: 2000, votes: 1,
                 downloads: 10, usability: 0.5, license: "CC0", tags: [], private: false, currentVersion: 2, version: 2,
                 pinned: false, url: "https://www.kaggle.com/datasets/x/y", description: "Fake.",
                 versions: [{number: 2, date: "2026-01-02"}, {number: 1, date: "2026-01-01"}],
                 files: [{name: "data.csv", size: 1000, format: "csv", importable: true},
                         {name: "more.parquet", size: 1000, format: "parquet", importable: true},
                         {name: "readme.txt", size: 10, format: null, importable: false}],
                 filesTruncated: false, importableFiles: 2});
  }
  if (path === "/plan") {
    return json({action: "create", reason: null, table: "KG.data", limit: 1000, version: 2,
                 columns: [{source: "text", name: "text_", type: "VARCHAR(4000)"}], warnings: [], download: {bytes: 1000}});
  }
  if (path.startsWith("/files?")) {
    return json({dataset: "x/y", version: 2, files: [{file: "data.csv", table: "KG.data", size: 1000, format: "csv"},
                                                      {file: "more.parquet", table: "KG.more", size: 1000, format: "parquet"}]});
  }
  if (path === "/jobs" && opts.method === "POST") {
    globalThis.lastStart = JSON.parse(opts.body);
    return json({id: "9", state: "queued"}, 202);
  }
  if (path === "/jobs/9") return json(job9[Math.min(job9Step++, job9.length - 1)]);
  return json({error: "unexpected " + path}, 500);
};
sessionStorage.setItem("kaggle2iris.auth", "x");

const {VERSION} = await import("../../web/js/state.js");
const imports = await import(`../../web/js/views/imports.js?v=${VERSION}`);
const dataset = await import(`../../web/js/views/dataset.js?v=${VERSION}`);

// --------------------------------------------------------- imports view
let stop = imports.render(main, {view: "imports", params: {}}, {});
await waitFor(() => byClass(main, "job").length, "running job");
const bar = find(main, e => e.getAttribute("role") === "progressbar");
check("running job shows progress", bar && bar.getAttribute("aria-valuenow") === "25" && /Writing rows: 1K of 4K rows \(5 s\)/.test(text(main)),
      bar && bar.getAttribute("aria-valuenow") + " " + text(main).slice(0, 200));
check("all-files job lists its tables", text(main).includes("all files") && text(main).includes("schema KG") &&
      Boolean(find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/tables/KG.b")));
check("accessibility with a running job", audit(main).length === 0, audit(main).join("; "));
const cancel = byText(main, "button", "Cancel");
check("cancel button names the job", cancel && cancel.getAttribute("aria-label") === "Cancel import 7");
cancel.click();
await waitFor(() => calls.includes("POST /jobs/7/cancel"), "cancel request");
await waitFor(() => byClass(main, "badge").some(b => b.textContent === "cancelled"), "cancelled state", 10000);
check("job ends as cancelled with the reason", text(main).includes("rolled back"));
const after = calls.length;
await new Promise(r => setTimeout(r, 2600));
check("polling stops once no job is running", calls.length === after, `${calls.length - after} more calls`);
stop();

// ------------------------------------------------- dataset import panel
stop = dataset.render(main, {view: "dataset", id: "x/y", params: {}}, {});
await waitFor(() => byText(main, "button", "Review plan"), "import panel");
check("accessibility of the dataset page", audit(main).length === 0, audit(main).join("; "));
check("import panel starts with the first file", byId(main, "imp-file").value === "data.csv" && byId(main, "imp-table").value === "KG.data");
check("Excel sheet field is hidden for CSV", find(main, e => e.getAttribute("for") === "imp-sheet").parentNode.hidden === true);
const start = byText(main, "button", "Start import");
byText(main, "button", "Review plan").click();
await waitFor(() => !start.disabled, "plan accepted");
check("plan enables Start import", text(main).includes("This import creates KG.data with up to 1K rows"));
start.click();
await waitFor(() => globalThis.lastStart, "job started");
check("start sends the import settings", JSON.stringify(globalThis.lastStart) ===
      JSON.stringify({dataset: "x/y", version: "", file: "data.csv", table: "KG.data", limit: 1000, mode: "replace", sheet: ""}),
      JSON.stringify(globalThis.lastStart));
await waitFor(() => /Downloading from Kaggle, 1 MB of 4 MB/.test(text(main)), "download progress", 10000);
check("panel shows download progress in bytes", true);
await waitFor(() => /Writing rows to IRIS, 2K of 4K rows/.test(text(main)), "insert progress", 10000);
check("panel shows insert progress", true);
await waitFor(() => text(main).includes("Imported 4K rows into KG.data"), "finished import", 10000);
check("finished import links to the table and to all imports",
      Boolean(find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/tables/KG.data")) &&
      Boolean(find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/imports")));
check("Review plan is usable again", byText(main, "button", "Review plan").disabled === false);

// all tabular files
const fileSelect = byId(main, "imp-file");
fileSelect.value = "*";
fileSelect.fire("change");
check("all files: the table becomes a schema", byId(main, "imp-table").value === "KG" && text(main).includes("Target schema"));
check("all files: snippet uses LoadAll", text(main).includes('Loader).LoadAll("x/y","",1000)') ||
      text(main).includes('Loader).LoadAll("x/y","KG",1000)'), text(byClass(main, "snippet")[0]));
byText(main, "button", "Review plan").click();
await waitFor(() => text(main).includes("This import creates 2 tables"), "file list", 10000);
check("all files: plan lists each file and its table", text(main).includes("more.parquet") && text(main).includes("KG.more"));
stop();

const failed = results.filter(r => !r).length;
console.log(`MOCKED ${failed ? "FAILED: " + failed + " of " + results.length : "PASSED: " + results.length + " checks"}`);
process.exit(failed ? 1 : 0);
