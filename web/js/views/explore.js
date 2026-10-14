// Explore: search Kaggle datasets through the IRIS backend.
import {api} from "../api.js?v=2";
import {badge, emptyState, errorPanel, h, mount, skeleton} from "../dom.js?v=2";
import {FILE_TYPES, LICENSES, SIZES, SORTS, buildHash, formatBytes, formatCount, timeAgo} from "../state.js?v=2";

const MAX_PAGES = 5;

export function render(main, route, {navigate, setTitle}) {
  if (setTitle) setTitle("Explore");
  const p = route.params;
  const state = {q: p.q || "", sort: SORTS.some(s => s[0] === p.sort) ? p.sort : "hottest", type: p.type || "",
                 license: p.license || "", size: p.size || "", tag: p.tag || "",
                 pages: Math.min(MAX_PAGES, Math.max(1, Number(p.pages) || 1))};
  let controller = null;
  let timer = null;
  let hasResults = false;

  const query = h("input", {type: "search", id: "q", class: "grow", value: state.q, autocomplete: "off",
                            placeholder: "Search by name or keyword", "aria-label": "Search datasets"});
  const sortButtons = SORTS.map(([value, label]) =>
    h("button", {type: "button", "aria-pressed": String(value === state.sort), text: label,
                 onclick: () => update({sort: value, pages: 1})}));
  const select = (id, label, options, value, key) => h("div", {class: "field"},
    h("label", {for: id, text: label}),
    h("select", {id, onchange: e => update({[key]: e.target.value, pages: 1})},
      h("option", {value: "", text: "Any"}),
      options.map(([v, text]) => h("option", {value: v, text, selected: v === value}))));
  const tag = h("input", {id: "tag", value: state.tag, placeholder: "e.g. finance", size: 12,
                          onchange: e => update({tag: e.target.value.trim(), pages: 1})});

  const results = h("div", {"aria-live": "polite", "aria-busy": "false"});
  mount(main, h("div", {class: "page"},
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {text: "Explore datasets"}),
        h("p", {text: "Search Kaggle, look inside a dataset's files, and import them into IRIS tables."}))),
    h("div", {class: "panel stack"},
      h("div", {class: "toolbar"}, query, h("div", {class: "segmented", role: "group", "aria-label": "Sort by"}, sortButtons)),
      h("div", {class: "toolbar"},
        select("type", "File type", FILE_TYPES, state.type, "type"),
        select("license", "License", LICENSES, state.license, "license"),
        select("size", "Size", SIZES.map(s => [s[0], s[1]]), state.size, "size"),
        h("div", {class: "field"}, h("label", {for: "tag", text: "Tag"}), tag))),
    results));

  query.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => update({q: query.value.trim(), pages: 1}), 300);
  });
  query.addEventListener("keydown", e => {
    if (e.key === "Enter") { clearTimeout(timer); update({q: query.value.trim(), pages: 1}); }
  });

  // Keeps the URL in step without re-rendering the whole view.
  function update(changes) {
    Object.assign(state, changes);
    sortButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(SORTS[i][0] === state.sort)));
    history.replaceState(null, "", buildHash("explore", {q: state.q, sort: state.sort === "hottest" ? "" : state.sort,
      type: state.type, license: state.license, size: state.size, tag: state.tag, pages: state.pages === 1 ? "" : state.pages}));
    load();
  }

  function params(page) {
    const size = SIZES.find(s => s[0] === state.size);
    return {search: state.q, sort: state.sort, fileType: state.type, license: state.license, tag: state.tag,
            minSize: size ? size[2] : "", maxSize: size ? size[3] : "", page};
  }

  // Kaggle returns 20 datasets a page; "Show more" loads the next page.
  async function load() {
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    results.setAttribute("aria-busy", "true");
    if (!hasResults) mount(results, h("div", {class: "results"}, Array.from({length: 6}, () => skeleton())));
    try {
      const items = [];
      let more = false, cached = true;
      for (let page = 1; page <= state.pages; page++) {
        const data = await api.search(params(page), signal);
        items.push(...data.items);
        more = data.hasMore;
        cached = cached && Boolean(data.cached);
        if (!more) break;
      }
      show(items, more, cached);
    } catch (e) {
      if (e.name === "AbortError") return;
      hasResults = false;
      mount(results, errorPanel(e, load, "Search failed"));
    } finally {
      if (!signal.aborted) results.setAttribute("aria-busy", "false");
    }
  }

  function show(items, more, cached) {
    const filtered = state.q || state.type || state.license || state.size || state.tag;
    hasResults = items.length > 0;
    if (!items.length) {
      mount(results, emptyState("No datasets found",
        filtered ? "Try fewer words, another spelling, or remove a filter." : "Kaggle returned no datasets.",
        filtered ? h("button", {class: "btn", text: "Clear search and filters",
                                onclick: () => navigate("explore", {})}) : null));
      return;
    }
    const heading = filtered ? `${items.length}${more ? "+" : ""} dataset${items.length === 1 ? "" : "s"}` :
      SORTS.find(s => s[0] === state.sort)[1] + " datasets";
    mount(results,
      h("div", {class: "stack"},
        h("div", {class: "row"}, h("span", {class: "count-line", text: heading}), h("span", {class: "spacer"}),
          cached ? h("span", {class: "count-line", text: "cached"}) : null),
        h("div", {class: "results"}, items.map(card)),
        more && state.pages < MAX_PAGES ? h("div", {class: "result-foot"},
          h("button", {class: "btn", text: "Show more", onclick: () => update({pages: state.pages + 1})})) : null));
  }

  function card(d) {
    const [owner, name] = d.ref.split("/");
    return h("a", {class: "card", href: buildHash("dataset", {}, d.ref)},
      h("div", {class: "card-title"}, d.title || name),
      h("div", {class: "owner mono", text: owner + "/" + name}),
      h("p", {class: "card-desc", text: d.subtitle || "No description."}),
      h("div", {class: "badges"},
        d.private ? badge("Private", "warn") : null,
        (d.tags || []).slice(0, 2).map(t => badge(t, "accent")),
        d.license ? badge(d.license) : null),
      h("div", {class: "card-meta"},
        h("span", {}, h("strong", {text: formatBytes(d.totalBytes)})),
        h("span", {}, h("strong", {text: formatCount(d.votes)}), " votes"),
        h("span", {}, h("strong", {text: formatCount(d.downloads)}), " downloads"),
        d.lastUpdated ? h("span", {text: "updated " + timeAgo(d.lastUpdated)}) : null));
  }

  load();
  return () => { clearTimeout(timer); if (controller) controller.abort(); };
}
