/* techdash front end.
   Feed content is untrusted, so every string reaches the DOM via textContent --
   nothing here builds markup by concatenation. */

const LS = {
  lastSeen: "techdash:lastSeen",
  theme: "techdash:theme",
  view: "techdash:view",
  tab: "techdash:tab",
};

// #market, #releases, ... deep-link a tab, so individual views are bookmarkable.
const hashTab = location.hash.replace("#", "");

const CARD_PREVIEW = 5; // items shown per card before the "+N more" expander

const state = {
  data: null,
  brief: null,
  expanded: new Set(),
  tab: hashTab || localStorage.getItem(LS.tab) || "brief",
  query: "",
  tags: new Set(),
  stableOnly: true,
  newOnly: false,
  view: localStorage.getItem(LS.view) || "cards",
  lastSeen: Number(localStorage.getItem(LS.lastSeen) || 0),
  firehoseLimit: 300,
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

// ---------------------------------------------------------------- formatting

function ago(seconds) {
  if (!seconds) return "";
  const delta = Date.now() / 1000 - seconds;
  if (delta < 0) return "now";
  if (delta < 90) return "just now";
  const units = [[60, "m", 60], [3600, "h", 24], [86400, "d", 30], [2592000, "mo", 12]];
  for (const [size, label, span] of units) {
    const value = delta / size;
    if (value < span) return `${Math.round(value)}${label}`;
  }
  return `${Math.round(delta / 31536000)}y`;
}

function dayLabel(seconds) {
  if (!seconds) return "undated";
  const date = new Date(seconds * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(date, today)) return "today";
  if (same(date, yesterday)) return "yesterday";
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

const isNew = (item) => Boolean(state.lastSeen && item.ts && item.ts > state.lastSeen);

// ---------------------------------------------------------------- filtering

function matches(item) {
  if (state.tab !== "all" && item.sec !== state.tab) return false;
  if (state.stableOnly && item.pre) return false;
  if (state.newOnly && !isNew(item)) return false;
  if (state.tags.size && !state.tags.has(item.tag)) return false;
  if (state.query) {
    const haystack = `${item.title} ${item.sum || ""} ${item.src} ${item.tag}`.toLowerCase();
    if (!state.query.split(/\s+/).every((word) => haystack.includes(word))) return false;
  }
  return true;
}

const visibleItems = () => (state.data ? state.data.items.filter(matches) : []);

// ---------------------------------------------------------------- rendering

function itemRow(item, showSource) {
  const link = el("a", "row" + (isNew(item) ? " is-new" : ""));
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";

  const title = el("span", "t");
  title.append(el("span", "dot"), document.createTextNode(item.title));
  link.append(title);

  if (item.sum) link.append(el("p", "blurb", item.sum));

  const sub = el("div", "sub");
  if (showSource) sub.append(el("span", "src", item.src));
  if (item.meta) sub.append(el("span", null, item.meta));
  if (item.tag) sub.append(el("span", "tag", item.tag));
  if (item.ts) sub.append(el("span", null, ago(item.ts)));
  if (item.pre) sub.append(el("span", "pre-badge", "pre"));
  if (item.discuss) {
    const discuss = el("a", null, "hn ↗");
    discuss.href = item.discuss;
    discuss.target = "_blank";
    discuss.rel = "noopener noreferrer";
    discuss.addEventListener("click", (event) => event.stopPropagation());
    sub.append(discuss);
  }
  link.append(sub);
  return link;
}

function tickerTiles(items) {
  const grid = el("div", "tickers");
  const ordered = [...items].sort((a, b) => (a.quote?.ord ?? 0) - (b.quote?.ord ?? 0));
  for (const item of ordered) {
    if (!item.quote) continue;
    const { symbol, price, change } = item.quote;
    const tile = el("a", "tile");
    tile.href = item.url;
    tile.target = "_blank";
    tile.rel = "noopener noreferrer";
    tile.append(el("span", "sym", symbol));
    tile.append(el("span", "name", item.title));
    tile.append(el("span", "px", price.toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    })));
    // Arrow + signed number carry the direction; color only reinforces it.
    const direction = change > 0.01 ? "up" : change < -0.01 ? "down" : "flat";
    const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "▪";
    tile.append(el("span", `chg ${direction}`, `${arrow} ${change > 0 ? "+" : ""}${change.toFixed(2)}%`));
    grid.append(tile);
  }
  return grid.children.length ? grid : null;
}

function sourceCard(source, items) {
  const card = el("article", "card");
  const head = el("div", "card-head");
  head.append(el("h3", null, source.name));
  head.append(el("span", "pill", String(items.length)));

  const age = el("span", "age", source.fetched_at ? ago(source.fetched_at) : "—");
  if (source.error) {
    head.classList.add("stale");
    age.textContent = source.error;
    age.title = source.note || source.error;
  }
  head.append(age);
  card.append(head);

  // Long cards are the main source of scroll. Show a preview, keep the rest
  // one click away rather than hiding it behind a tab switch.
  // A search or tag filter has already narrowed things, so show everything that
  // survived it -- but the collapse control belongs only to a reader who chose
  // to expand, otherwise "show less" would be a button that does nothing.
  const byChoice = state.expanded.has(source.id);
  const filtering = Boolean(state.query) || state.tags.size > 0;
  const shown = byChoice || filtering ? items : items.slice(0, CARD_PREVIEW);

  const list = el("ul");
  for (const item of shown) {
    const row = el("li");
    row.append(itemRow(item, false));
    list.append(row);
  }
  card.append(list);

  if (items.length > shown.length) {
    const more = el("button", "more-btn", `+${items.length - shown.length} more`);
    more.addEventListener("click", () => {
      state.expanded.add(source.id);
      render();
    });
    card.append(more);
  } else if (byChoice && items.length > CARD_PREVIEW) {
    const less = el("button", "more-btn", "show less");
    less.addEventListener("click", () => {
      state.expanded.delete(source.id);
      render();
    });
    card.append(less);
  }
  return card;
}

// ---------------------------------------------------------------- brief

const BRIEF_SOFT_STALE_HOURS = 6;
const BRIEF_HARD_STALE_HOURS = 24;

/* The brief is written by hand (via Claude Code) and never regenerates itself,
   so the page has to say when it has fallen behind the feeds under it. */
function briefFreshness(brief) {
  if (!brief?.built_at) return null;
  const ageHours = (Date.now() / 1000 - brief.built_at) / 3600;
  const since = state.data
    ? state.data.items.filter((item) => item.ts && item.ts > brief.built_at).length
    : 0;
  return {
    ageHours,
    since,
    soft: ageHours >= BRIEF_SOFT_STALE_HOURS,
    hard: ageHours >= BRIEF_HARD_STALE_HOURS,
  };
}

function staleBanner(fresh) {
  const banner = el("div", "brief-stale");
  banner.append(el("strong", null,
    `This brief is ${ago(state.brief.built_at)} old — ${fresh.since.toLocaleString()} stories have landed since.`));
  const how = el("p");
  how.append(document.createTextNode("Rebuild it: "));
  how.append(el("code", null, "techdash brief"));
  how.append(document.createTextNode(", then "));
  how.append(el("code", null, "/brief"));
  how.append(document.createTextNode(" in Claude Code."));
  banner.append(how);
  return banner;
}

function renderBrief(main) {
  const brief = state.brief;
  if (!brief || brief.empty) {
    const empty = el("div", "brief-empty");
    empty.append(el("h2", null, "No brief yet"));
    empty.append(el("p", null,
      "The brief is written by Claude Code from the feeds below — no API key, no cost."));
    const steps = el("ol", "brief-steps");
    const one = el("li");
    one.append(document.createTextNode("Run "));
    one.append(el("code", null, "~/techdash/techdash brief"));
    one.append(document.createTextNode(" to export the digest."));
    const two = el("li");
    two.append(document.createTextNode("In Claude Code, run "));
    two.append(el("code", null, "/brief"));
    two.append(document.createTextNode("."));
    steps.append(one, two, el("li", null, "Reload this page."));
    empty.append(steps);
    main.append(empty);
    return;
  }

  const wrap = el("div", "brief");
  const fresh = briefFreshness(brief);
  if (fresh?.hard) wrap.append(staleBanner(fresh));

  const head = el("div", "brief-head");
  head.append(el("h1", null, brief.headline || "Today"));
  const age = brief.built_at ? ago(brief.built_at) : "";
  const built = age === "just now" ? "built just now" : age ? `built ${age} ago` : "";
  const parts = [`${brief.items?.length || 0} things that matter`,
                 `last ${brief.window_hours || 24}h`, built].filter(Boolean);
  if (fresh?.since) parts.push(`${fresh.since.toLocaleString()} new since`);
  const meta = el("p", "brief-meta", parts.join(" · "));
  if (fresh?.soft) meta.classList.add("stale");
  head.append(meta);
  wrap.append(head);

  for (const item of brief.items || []) {
    const block = el("article", "brief-item");
    const heading = el("h2");
    if (item.tag) heading.append(el("span", "brief-tag", item.tag));
    heading.append(document.createTextNode(item.title));
    block.append(heading);
    if (item.why) block.append(el("p", null, item.why));

    if (item.links?.length) {
      const links = el("div", "brief-links");
      for (const link of item.links) {
        const anchor = el("a", null, link.src);
        anchor.href = link.url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        links.append(anchor);
      }
      block.append(links);
    }
    wrap.append(block);
  }

  if (brief.also?.length) {
    const also = el("div", "brief-also");
    also.append(el("h3", null, "Also"));
    const list = el("ul");
    for (const line of brief.also) list.append(el("li", null, line));
    also.append(list);
    wrap.append(also);
  }
  main.append(wrap);
}

function renderCards(main, items) {
  const bySource = new Map();
  for (const item of items) {
    if (!bySource.has(item.sid)) bySource.set(item.sid, []);
    bySource.get(item.sid).push(item);
  }

  const sections = state.data.sections.filter(
    (section) => state.tab === "all" || section.id === state.tab,
  );

  for (const section of sections) {
    // A card with nothing in it is noise -- a dead or still-loading source is
    // reported once in the footer's health list, not as an empty column item.
    const sources = state.data.health.filter((source) => source.section === section.id);
    const populated = sources.filter((source) => (bySource.get(source.id) || []).length);
    if (!populated.length) continue;

    const head = el("div", "section-head");
    head.append(el("h2", null, section.label));
    head.append(el("p", null, section.blurb));
    main.append(head);

    if (section.id === "market") {
      const tickers = bySource.get("tickers") || [];
      const tiles = tickerTiles(tickers);
      if (tiles) main.append(tiles);
    }

    const grid = el("div", "grid");
    for (const source of populated) {
      if (source.id === "tickers") continue;
      grid.append(sourceCard(source, bySource.get(source.id)));
    }
    main.append(grid);
  }
}

/* A pure time sort puts 16 tickers and 30 npm publishes — all stamped "now" —
   above every headline. Tickers belong in the Market tiles, and bulk registry
   feeds get a per-source quota so the stream stays varied. */
function firehoseItems(items) {
  const quota = Math.max(4, Math.ceil(state.firehoseLimit / 60));
  const used = new Map();
  const kept = [];
  for (const item of items) {
    if (item.sid === "tickers") continue;
    const count = used.get(item.sid) || 0;
    if (count >= quota) continue;
    used.set(item.sid, count + 1);
    kept.push(item);
  }
  return kept;
}

function renderFirehose(main, allItems) {
  const wrap = el("div", "firehose");
  const items = firehoseItems(allItems);
  const shown = items.slice(0, state.firehoseLimit);
  let currentDay = null;
  let card = null;
  let list = null;

  for (const item of shown) {
    const day = dayLabel(item.ts);
    if (day !== currentDay) {
      currentDay = day;
      wrap.append(el("div", "daybreak", day));
      card = el("article", "card");
      list = el("ul");
      card.append(list);
      wrap.append(card);
    }
    const row = el("li");
    row.append(itemRow(item, true));
    list.append(row);
  }

  main.append(wrap);
  if (items.length > shown.length) {
    const more = el("button", "ghost-btn", `show ${items.length - shown.length} more`);
    more.style.margin = "16px auto";
    more.style.display = "block";
    more.addEventListener("click", () => {
      state.firehoseLimit += 500;
      render();
    });
    main.append(more);
  }
}

function renderTabs() {
  const tabs = $("tabs");
  tabs.replaceChildren();
  const entries = [
    { id: "brief", label: "Brief" },
    { id: "all", label: "All" },
    ...state.data.sections,
  ];

  for (const [index, section] of entries.entries()) {
    const tab = el("button", "tab");
    tab.setAttribute("aria-selected", String(state.tab === section.id));
    tab.append(document.createTextNode(section.label));

    if (section.id === "brief") {
      const count = state.brief?.items?.length;
      if (count) tab.append(el("span", "pill", String(count)));
      const fresh = briefFreshness(state.brief);
      if (fresh?.hard) {
        tab.append(el("span", "stale-dot"));
        tab.title = `Brief is ${ago(state.brief.built_at)} old — rebuild with "techdash brief" then /brief`;
      }
    } else {
      const count = state.data.items.filter(
        (item) => (section.id === "all" || item.sec === section.id) && !(state.stableOnly && item.pre),
      ).length;
      tab.append(el("span", "pill", String(count)));
    }
    tab.title = `${section.label} — ${index + 1}`;
    tab.addEventListener("click", () => {
      state.tab = section.id;
      state.tags.clear();
      state.expanded.clear();
      state.firehoseLimit = 300;
      localStorage.setItem(LS.tab, section.id);
      history.replaceState(null, "", section.id === "all" ? " " : `#${section.id}`);
      render();
      window.scrollTo({ top: 0 });
    });
    tabs.append(tab);
  }
}

function renderTagbar() {
  const bar = $("tagbar");
  bar.replaceChildren();

  const counts = new Map();
  for (const item of state.data.items) {
    if (state.tab !== "all" && item.sec !== state.tab) continue;
    if (state.stableOnly && item.pre) continue;
    if (item.tag) counts.set(item.tag, (counts.get(item.tag) || 0) + 1);
  }

  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20);
  for (const [tag, count] of ranked) {
    const chip = el("button", "chip", `${tag} ${count}`);
    chip.setAttribute("aria-pressed", String(state.tags.has(tag)));
    chip.addEventListener("click", () => {
      state.tags.has(tag) ? state.tags.delete(tag) : state.tags.add(tag);
      render();
    });
    bar.append(chip);
  }
}

function renderHealth() {
  const box = $("health");
  box.replaceChildren();
  const rows = [...state.data.health].sort((a, b) => a.name.localeCompare(b.name));
  for (const source of rows) {
    const row = el("div", source.error ? "bad" : "ok");
    row.append(el("span", null, source.name));
    row.append(el("span", null, source.error ? source.error : `${source.count} · ${ago(source.fetched_at)}`));
    if (source.note) row.title = source.note;
    box.append(row);
  }
  const down = rows.filter((source) => source.error).length;
  $("health-summary").textContent =
    `source health — ${rows.length - down}/${rows.length} live${down ? `, ${down} quiet` : ""}`;
}

function renderStatus() {
  const health = state.data.health;
  const down = health.filter((source) => source.error).length;
  const status = $("status");
  status.textContent = `${health.length - down}/${health.length} live · ${ago(state.data.generated_at)}`;
  status.classList.toggle("warn", down > health.length / 3);

  const fresh = state.data.items.filter((item) => isNew(item) && !(state.stableOnly && item.pre)).length;
  $("new-count").textContent = String(fresh);
}

function render() {
  if (!state.data) return;
  const main = $("main");
  const scroll = window.scrollY;
  main.replaceChildren();

  renderTabs();
  renderTagbar();
  renderStatus();
  renderHealth();

  // Tag chips and the stable/new switches filter the feed, not the brief.
  document.querySelector(".filter-row").hidden = state.tab === "brief";

  if (state.tab === "brief") {
    renderBrief(main);
    window.scrollTo({ top: scroll });
    return;
  }

  const items = visibleItems();
  if (!items.length) {
    main.append(el("p", "empty", "Nothing matches those filters."));
  } else if (state.view === "firehose") {
    renderFirehose(main, items);
  } else {
    renderCards(main, items);
  }
  window.scrollTo({ top: scroll });
}

// ---------------------------------------------------------------- data + wiring

async function load() {
  const [data, brief] = await Promise.all([
    fetch("/api/data", { cache: "no-store" }).then((r) => r.json()),
    fetch("/api/brief", { cache: "no-store" }).then((r) => r.json()).catch(() => null),
  ]);
  state.data = data;
  state.brief = brief;
  render();
}

async function forceRefresh() {
  const button = $("refresh");
  button.classList.add("spinning");
  try {
    await fetch("/api/refresh", { method: "POST" });
    // Feeds land over the next few seconds; poll rather than guess.
    for (const delay of [2500, 5000, 9000, 15000]) {
      await new Promise((resolve) => setTimeout(resolve, delay));
      await load();
    }
  } finally {
    button.classList.remove("spinning");
  }
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(LS.theme, theme);
}

function wire() {
  setTheme(localStorage.getItem(LS.theme) || "dark");

  const search = $("search");
  search.addEventListener("input", () => {
    state.query = search.value.trim().toLowerCase();
    $("clear-search").hidden = !search.value;
    render();
  });

  $("clear-search").addEventListener("click", () => {
    search.value = "";
    state.query = "";
    $("clear-search").hidden = true;
    render();
    search.focus();
  });

  $("stable-only").addEventListener("change", (event) => {
    state.stableOnly = event.target.checked;
    render();
  });

  $("new-only").addEventListener("change", (event) => {
    state.newOnly = event.target.checked;
    render();
  });

  $("refresh").addEventListener("click", forceRefresh);
  $("theme-toggle").addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  $("view-toggle").addEventListener("click", () => {
    state.view = state.view === "cards" ? "firehose" : "cards";
    localStorage.setItem(LS.view, state.view);
    $("view-toggle").textContent = state.view === "cards" ? "☰" : "▦";
    render();
  });
  $("view-toggle").textContent = state.view === "cards" ? "☰" : "▦";

  // Clicking the "new" counter marks everything as read.
  $("new-count").addEventListener("click", (event) => {
    event.preventDefault();
    state.lastSeen = Date.now() / 1000;
    localStorage.setItem(LS.lastSeen, String(state.lastSeen));
    render();
  });

  document.addEventListener("keydown", (event) => {
    const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
    if (event.key === "/" && !typing) {
      event.preventDefault();
      search.focus();
      return;
    }
    if (event.key === "Escape") {
      search.value = "";
      state.query = "";
      state.tags.clear();
      $("clear-search").hidden = true;
      search.blur();
      render();
      return;
    }
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.key === "r") forceRefresh();
    else if (event.key === "t") $("theme-toggle").click();
    else if (event.key === "v") $("view-toggle").click();
    else if (/^[1-8]$/.test(event.key)) {
      const tabs = $("tabs").children;
      const target = tabs[Number(event.key) - 1];
      if (target) target.click();
    }
  });

  // The hash is read once at startup for deep links; this makes editing it on a
  // live page work too. Tab clicks use replaceState, which fires no hashchange.
  window.addEventListener("hashchange", () => {
    const wanted = location.hash.replace("#", "") || "all";
    const known = ["brief", "all", ...(state.data?.sections || []).map((s) => s.id)];
    if (wanted === state.tab || !known.includes(wanted)) return;
    state.tab = wanted;
    state.tags.clear();
    state.expanded.clear();
    localStorage.setItem(LS.tab, wanted);
    render();
    window.scrollTo({ top: 0 });
  });

  // Remember where the reader got to, so "new" means new since they left.
  const markSeen = () => localStorage.setItem(LS.lastSeen, String(Date.now() / 1000));
  window.addEventListener("beforeunload", markSeen);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") markSeen();
  });
}

wire();
load();
setInterval(load, 90000);
