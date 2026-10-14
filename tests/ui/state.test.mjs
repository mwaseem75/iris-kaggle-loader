// node --test tests/ui/state.test.mjs
import assert from "node:assert/strict";
import {readFileSync, readdirSync, statSync} from "node:fs";
import {join, dirname} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";
import {VERSION, amountDone, buildHash, defaultTable, formatBytes, formatCount, objectScriptCall, parseHash, sqlCall,
        timeAgo, usability} from "../../web/js/state.js";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "web");

test("routes", () => {
  assert.deepEqual(parseHash(""), {view: "explore", id: null, params: {}});
  assert.deepEqual(parseHash("#/explore?q=titanic&sort=votes"), {view: "explore", id: null, params: {q: "titanic", sort: "votes"}});
  assert.deepEqual(parseHash("#/dataset/uciml/iris?tab=preview&offset=20"),
                   {view: "dataset", id: "uciml/iris", params: {tab: "preview", offset: "20"}});
  assert.equal(parseHash("#/nonsense").view, "explore");
  assert.equal(buildHash("explore", {q: "a b", sort: "", type: null}), "#/explore?q=a+b");
  assert.equal(buildHash("dataset", {tab: "schema"}, "uciml/iris"), "#/dataset/uciml/iris?tab=schema");
  const hash = buildHash("dataset", {file: "data/train set.csv", version: 2}, "a/b");
  assert.deepEqual(parseHash(hash), {view: "dataset", id: "a/b", params: {file: "data/train set.csv", version: "2"}});
});

test("snippets", () => {
  assert.equal(objectScriptCall({dataset: "uciml/iris", file: "Iris.csv"}),
               'do ##class(dc.kaggle2iris.Loader).Load("uciml/iris","Iris.csv")');
  assert.equal(objectScriptCall({dataset: "uciml/iris", file: "Iris.csv", table: "Demo.Iris", limit: 1000}),
               'do ##class(dc.kaggle2iris.Loader).Load("uciml/iris","Iris.csv","Demo.Iris",1000)');
  assert.equal(objectScriptCall({dataset: "a/b", file: "book.xlsx", version: 3, sheet: "Q1"}),
               'do ##class(dc.kaggle2iris.Loader).Load("a/b","book.xlsx","",0,"replace","3","Q1")');
  assert.equal(objectScriptCall({dataset: 'a/b"c', file: "x.csv"}), 'do ##class(dc.kaggle2iris.Loader).Load("a/b""c","x.csv")');
  assert.equal(objectScriptCall({dataset: "a/b", file: "*", table: "Shop", limit: 500}),
               'do ##class(dc.kaggle2iris.Loader).LoadAll("a/b","Shop",500)');
  assert.equal(objectScriptCall({dataset: "a/b", file: "*"}), 'do ##class(dc.kaggle2iris.Loader).LoadAll("a/b")');
  assert.equal(sqlCall({dataset: "uciml/iris", file: "Iris.csv", table: "Demo.I", limit: 50}),
               "SELECT dc_kaggle2iris.load_dataset('uciml/iris', 'Iris.csv', 'Demo.I', 50)");
  assert.equal(sqlCall({dataset: "x/y", file: "f.csv", version: "2"}),
               "SELECT dc_kaggle2iris.load_dataset('x/y', 'f.csv', NULL, 0, 'replace', '2')");
  assert.equal(sqlCall({dataset: "o'k/y", file: "f.csv"}), "SELECT dc_kaggle2iris.load_dataset('o''k/y', 'f.csv')");
  assert.equal(sqlCall({dataset: "x/y", file: "*"}), null);
});

test("formatting", () => {
  assert.equal(formatCount(999), "999");
  assert.equal(formatCount(173262), "173K");
  assert.equal(formatCount(1287), "1.3K");
  assert.equal(formatCount(14868862), "14.9M");
  assert.equal(formatCount(null), "-");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(83446840), "79.6 MB");
  const now = Date.parse("2026-10-13T12:00:00Z");
  assert.equal(timeAgo("2026-10-13T11:59:59Z", now), "just now");
  assert.equal(timeAgo("2026-10-13T10:00:00Z", now), "2 hours ago");
  assert.equal(timeAgo("2026-09-13T12:00:00Z", now), "1 month ago");
  assert.equal(timeAgo("", now), "");
  assert.equal(defaultTable("Iris.csv"), "KG.Iris");
  assert.equal(defaultTable("data/rotten-tomatoes.v2.csv"), "KG.rotten_tomatoes_v2");
  assert.equal(usability(0.7941176), "7.9");
  assert.equal(usability(1), "10.0");
  assert.equal(usability(null), "-");
  assert.equal(amountDone({phase: "fetching", rowsDone: 1048576, rowsTotal: 4194304}), "1 MB of 4 MB");
  assert.equal(amountDone({phase: "inserting", rowsDone: 1000, rowsTotal: 4000}), "1K of 4K rows");
  assert.equal(amountDone({phase: "resolving", rowsDone: 0, rowsTotal: 0}), "");
});

function files(dir) {
  return readdirSync(dir).flatMap(f => statSync(join(dir, f)).isDirectory() ? files(join(dir, f)) : [join(dir, f)]);
}

test("web files are ASCII (IRIS serves them as ISO-8859-1)", () => {
  for (const f of files(WEB)) {
    const bad = [...readFileSync(f, "utf8")].findIndex(c => c.charCodeAt(0) > 127);
    assert.equal(bad, -1, `${f} has a non-ASCII character at ${bad}`);
  }
});

test("every script and stylesheet URL carries the same version", () => {
  const index = readFileSync(join(WEB, "index.html"), "utf8");
  assert.match(index, new RegExp(`app\\.css\\?v=${VERSION}"`));
  assert.match(index, new RegExp(`js/app\\.js\\?v=${VERSION}"`));
  for (const f of files(join(WEB, "js"))) {
    for (const [, spec] of readFileSync(f, "utf8").matchAll(/from "(\.[^"]+)"/g)) {
      assert.ok(spec.endsWith(`?v=${VERSION}`), `${f}: import "${spec}" should end with ?v=${VERSION}`);
    }
  }
});

test("routes for imports and tables", async () => {
  const s = await import("../../web/js/state.js");
  assert.deepEqual(s.parseHash("#/imports"), {view: "imports", id: null, params: {}});
  assert.deepEqual(s.parseHash("#/tables"), {view: "tables", id: null, params: {}});
  assert.deepEqual(s.parseHash("#/tables/KG.Iris?tab=columns"), {view: "tables", id: "KG.Iris", params: {tab: "columns"}});
  assert.equal(s.buildHash("tables", {offset: 50}, "KG.Iris"), "#/tables/KG.Iris?offset=50");
  assert.equal(s.themeFor("light"), "light");
  assert.equal(s.themeFor(null), "dark");
  assert.equal(s.serverTime("2026-10-12 22:41:22"), "2026-10-12 22:41");
});

function tokens(css, selector) {
  const block = css.slice(css.indexOf(selector)).match(/\{([^}]*)\}/)[1];
  return Object.fromEntries([...block.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
}

test("text contrast is at least 4.5:1 in both themes", async () => {
  const {contrast, parseColor} = await import("../../web/js/state.js");
  const css = readFileSync(join(WEB, "app.css"), "utf8");
  const dark = tokens(css, ':root, [data-theme="dark"]');
  const themes = {dark, light: {...dark, ...tokens(css, '[data-theme="light"]')}};
  for (const [name, t] of Object.entries(themes)) {
    const c = (key, under = "surface") => parseColor(t[key], parseColor(t[under]));
    const pairs = [
      ["text", "bg"], ["text", "surface"], ["text", "surface-2"], ["text", "surface-3"],
      ["text-2", "bg"], ["text-2", "surface"], ["text-2", "surface-2"],
      ["muted", "bg"], ["muted", "surface"], ["muted", "surface-2"],
      ["accent-strong", "bg"], ["accent-strong", "surface"], ["on-accent", "accent"],
      ["ok", "surface"], ["warn", "surface"], ["danger", "surface"]];
    for (const [fg, bg] of pairs) {
      const ratio = contrast(c(fg), c(bg));
      assert.ok(ratio >= 4.5, `${name}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`);
    }
    // badges and notices: colored text on a translucent tint over a panel
    for (const [fg, soft] of [["accent-strong", "accent-soft"], ["ok", "ok-soft"], ["warn", "warn-soft"], ["danger", "danger-soft"],
                              ["notice-ok", "ok-soft"], ["notice-warn", "warn-soft"], ["notice-danger", "danger-soft"]]) {
      const ratio = contrast(c(fg), c(soft));
      assert.ok(ratio >= 4.5, `${name}: --${fg} on --${soft} is ${ratio.toFixed(2)}:1`);
    }
    // non-text: focus ring and form control outlines need 3:1
    for (const [fg, bg] of [["focus", "bg"], ["focus", "surface"], ["input-border", "bg"], ["input-border", "surface"],
                            ["input-border", "surface-2"]]) {
      const ratio = contrast(c(fg), c(bg));
      assert.ok(ratio >= 3, `${name}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`);
    }
  }
});
