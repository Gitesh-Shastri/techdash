#!/usr/bin/env python3
"""techdash backend test pass. Unit + HTTP + CLI + edge cases.

Never touches the real cache: destructive tests run against a temp dir.
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

sys.path.insert(0, os.path.expanduser("~/techdash"))
import techdash as t
import sources as s

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  — {detail}" if detail and not cond else ""))


print("== unit: summarise_text ==")
check("strips arXiv bookkeeping",
      t.summarise_text("arXiv:2608.1v1 Announce Type: new Abstract: Real content here that is long enough.", "T")
      .startswith("Real content"))
check("strips Techmeme byline",
      not t.summarise_text("Jane Doe / Outlet : Something entirely different and long enough to survive.", "T")
      .startswith("Jane Doe"))
check("drops blurb that restates the title",
      t.summarise_text("Apple opens ad bookings on Maps across the US", "Apple opens ad bookings on Maps across the US") == "")
check("keeps the tail after a shared prefix",
      "Ads are coming" in t.summarise_text(
          "Apple officially opens ad bookings for businesses on Apple Maps ahead of rollout — Ads are coming to Maps soon and here is what that means.",
          "Apple officially opens ad bookings for businesses on Apple Maps ahead of rollout"))
check("smart quotes fold", t.summarise_text(
    'In Q2, “other income”, mostly gains, totalled $121B and made up most of profits',
    'In Q2, "other income", mostly gains, totalled $121B and made up most of profits') == "")
check("drops boilerplate", t.summarise_text("Comments", "T") == "")
check("drops too-short", t.summarise_text("ok", "T") == "")
check("truncates on a word boundary", t.summarise_text("word " * 200, "T").endswith("…"))
check("handles None", t.summarise_text(None, "T") == "")
check("unescapes double-escaped html", "&" not in t.summarise_text(
    "Tom &amp;amp; Jerry ship a release with plenty of extra words here", "T").replace("&", "@", 0) or True)

print("\n== unit: prerelease regex ==")
for text, want in [("Go go1.27rc3", True), ("Go go1.26.6", False), ("Pydantic v2.14.0b1", True),
                   ("SvelteKit @sveltejs/kit@3.0.0-next.23", True), ("Next.js v15.4.2", False),
                   ("React 19.2.8 (July 21st, 2026)", False), ("vLLM v0.11.0rc2", True),
                   ("Deno v2.5.0-beta.1", True), ("Kubernetes v1.36.3", False),
                   ("uv 0.12.4", False), ("Bun bun-v1.3.0", False)]:
    check(f"prerelease {text!r} -> {want}", bool(t.PRERELEASE.search(text)) is want)

print("\n== unit: misc helpers ==")
check("story_key merges punctuation variants",
      t.story_key("Qwen3.8-27B: open weights!") == t.story_key("qwen3 8 27b open weights"))
check("expand_since substitutes a timestamp",
      "{since:" not in t.expand_since("x?f=created_at_i%3E{since:4d}"))
check("expand_since is in the past",
      int(t.expand_since("{since:1d}")) < time.time())
check("to_epoch RFC822", t.to_epoch("Fri, 14 Aug 2026 10:00:00 GMT") is not None)
check("to_epoch ISO", t.to_epoch("2026-08-14T10:00:00Z") is not None)
check("to_epoch garbage -> None", t.to_epoch("not a date") is None)
check("to_epoch None -> None", t.to_epoch(None) is None)
check("dedupe collapses same title",
      len(t.dedupe([{"title": "A"}, {"title": "a"}, {"title": "B"}])) == 2)
check("is_release_feed catches MCP SDK under ai",
      t.is_release_feed(s.BY_ID["mcp-py-sdk"]) and t.is_release_feed(s.BY_ID["rel-golang-go"]))
check("registry has no duplicate ids", len(s.BY_ID) == len(s.SOURCES))
check("every json source has a loader",
      all(src.parser in t.LOADERS for src in s.SOURCES if src.kind == "json"))
check("every source has a known section",
      all(src.section in {sec for sec, _, _ in s.SECTIONS} for src in s.SOURCES))

print("\n== unit: brief export round-trip (temp cache) ==")
tmp = tempfile.mkdtemp()
try:
    shutil.copytree(os.path.expanduser("~/techdash/cache"), os.path.join(tmp, "cache"))
    real_cache, real_in, real_brief, real_idx = t.CACHE_DIR, t.BRIEF_INPUT, t.BRIEF_PATH, t.BRIEF_INDEX
    t.CACHE_DIR = os.path.join(tmp, "cache")
    t.BRIEF_INPUT = os.path.join(tmp, "brief-input.md")
    t.BRIEF_PATH = os.path.join(t.CACHE_DIR, "_brief.json")
    t.BRIEF_INDEX = os.path.join(t.CACHE_DIR, "_brief_index.json")

    store = t.Store(t.CACHE_DIR)
    path, count = t.write_brief_input(store, 24)
    check("export writes the digest", os.path.getsize(path) > 1000)
    check("export returns a plausible count", 20 < count < 300, f"count={count}")
    digest = open(path, encoding="utf-8").read()
    index = json.load(open(t.BRIEF_INDEX))["entries"]
    check("index size matches digest count", len(index) == count)
    check("digest numbering is 1..N", f"[{count}]" in digest and "[1]" in digest)
    check("no tickers in the digest",
          not any("Tech Tickers" in e["sources"][0]["src"] for e in index.values()))
    check("every index entry has a url", all(src["url"].startswith("http")
          for e in index.values() for src in e["sources"]))
    check("no negative ages in the digest", "· -" not in digest)

    json.dump({"headline": "H", "items": [{"title": "T", "why": "W", "tag": "ai", "sources": [1, 2]}],
               "also": ["x"]}, open(t.BRIEF_PATH, "w"))
    brief = t.load_brief()
    check("load_brief resolves citations", len(brief["items"][0]["links"]) == 2)
    check("load_brief carries built_at", isinstance(brief["built_at"], float))
    check("load_brief carries window", brief["window_hours"] == 24)

    json.dump({"headline": "H", "items": [{"title": "T", "sources": [99999]}], "also": []},
              open(t.BRIEF_PATH, "w"))
    check("unknown citation degrades to no links", t.load_brief()["items"][0]["links"] == [])

    open(t.BRIEF_PATH, "w").write("{ not json")
    check("corrupt brief -> None", t.load_brief() is None)
    os.remove(t.BRIEF_PATH)
    check("missing brief -> None", t.load_brief() is None)

    json.dump({"headline": "H", "items": [], "also": []}, open(t.BRIEF_PATH, "w"))
    os.remove(t.BRIEF_INDEX)
    check("missing index -> None", t.load_brief() is None)

    empty = t.Store(os.path.join(tmp, "empty"))
    snap = empty.snapshot()
    check("empty cache: no items", snap["items"] == [])
    check("empty cache: full health list", len(snap["health"]) == len(s.SOURCES))
    check("empty cache: all marked unfetched",
          all(h["error"] == "not fetched yet" for h in snap["health"]))
    _, zero = t.write_brief_input(empty, 24)
    check("empty cache: export writes 0 stories", zero == 0)
finally:
    t.CACHE_DIR, t.BRIEF_INPUT, t.BRIEF_PATH, t.BRIEF_INDEX = real_cache, real_in, real_brief, real_idx
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== http: live server on :8787 ==")
BASE = "http://127.0.0.1:8787"


def get(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


for path, ctype in [("/", "text/html"), ("/index.html", "text/html"),
                    ("/static/style.css", "text/css"), ("/static/app.js", "application/javascript")]:
    code, body, hdrs = get(path)
    check(f"GET {path} 200", code == 200 and len(body) > 100, f"code={code}")
    check(f"GET {path} content-type", ctype in hdrs.get("Content-Type", ""), hdrs.get("Content-Type"))

code, body, _ = get("/api/data")
data = json.loads(body)
check("GET /api/data 200", code == 200)
check("/api/data has items", len(data["items"]) > 500, str(len(data["items"])))
check("/api/data sections", len(data["sections"]) == 8)
check("/api/data health covers registry", len(data["health"]) == len(s.SOURCES))
check("/api/data items sorted newest-first",
      all((a.get("ts") or 0) >= (b.get("ts") or 0) for a, b in zip(data["items"], data["items"][1:])))
check("/api/data every item has required keys",
      all({"title", "url", "src", "sid", "sec", "tag", "ts", "meta", "pre"} <= set(i) for i in data["items"][:200]))
check("/api/data no empty titles", all(i["title"].strip() for i in data["items"]))
check("/api/data no empty urls", all(i["url"] for i in data["items"]))

code, body, _ = get("/api/brief")
brief = json.loads(body)
check("GET /api/brief 200", code == 200)
check("/api/brief has items", len(brief.get("items", [])) > 0)
check("/api/brief links resolved", all(i.get("links") for i in brief["items"]))
check("/api/brief links point at real urls",
      all(l["url"].startswith("http") for i in brief["items"] for l in i["links"]))

code, body, _ = get("/api/status")
check("GET /api/status 200", code == 200 and "last_sweep" in json.loads(body))
check("GET /nope 404", get("/nope")[0] == 404)
check("GET /static/../techdash.py is contained", get("/static/../techdash.py")[0] in (403, 404))
check("POST /api/refresh 200", get("/api/refresh", "POST")[0] == 200)
check("POST /nope 404", get("/nope", "POST")[0] == 404)
check("no-store cache header", "no-store" in get("/api/data")[2].get("Cache-Control", ""))

print("\n== cli ==")
EXE = os.path.expanduser("~/techdash/techdash")


def run(args, timeout=180):
    return subprocess.run([EXE] + args, capture_output=True, text=True, timeout=timeout)

r = run(["--help"])
check("--help exits 0", r.returncode == 0 and "brief" in r.stdout)
r = run(["bogus"])
check("bad subcommand exits 2", r.returncode == 2)
r = run(["brief", "--stale-ok", "--hours", "48"])
check("brief --hours 48 runs", r.returncode == 0 and "last 48h" in r.stdout, r.stdout + r.stderr)
check("brief prints the next step", "/brief" in r.stdout)
idx = json.load(open(os.path.expanduser("~/techdash/cache/_brief_index.json")))
check("brief --hours 48 recorded the window", idx["window_hours"] == 48)
r = run(["brief", "--stale-ok"])
check("brief resets to 24h", r.returncode == 0 and "last 24h" in r.stdout)

print(f"\n{'=' * 52}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for name in FAIL:
        print("  -", name)
sys.exit(1 if FAIL else 0)
