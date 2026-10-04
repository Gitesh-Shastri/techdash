# techdash

One local page for everything happening in tech. 123 sources — Hacker News, the
tech press, stock tickers, package registries, GitHub releases for your stack,
the MCP ecosystem, AI labs, papers, forums — pulled server-side, cached on disk,
served at `http://localhost:8787`.

Python standard library only. No `pip install`, no API keys, no accounts.

## Run it

```sh
~/techdash/techdash            # serve on :8787 and open a browser
~/techdash/techdash --no-open  # serve without opening a browser
~/techdash/techdash --port 9000
~/techdash/techdash --once     # refresh the cache and exit (for cron)
~/techdash/techdash brief      # export a digest for Claude Code to summarize
```

Put it on your PATH once and it's a single word from anywhere:

```sh
ln -s ~/techdash/techdash /usr/local/bin/techdash
```

The first launch serves whatever is already cached and fills in as feeds land —
a cold start reaches all 123 live sources in about 25 seconds.

### Keep it warm in the background

Refresh the cache hourly so the page is instant whenever you open it:

```sh
crontab -e
# then add:
0 * * * * /Users/giteshshastri/techdash/techdash --once >/dev/null 2>&1
```

A `--once` run takes about 25 seconds.

## The daily brief

The landing tab is a written brief — 8-ish things that actually matter, with one
line each on why, and links. It exists so you read one screen instead of
scrolling 1,300 items.

**There is no API key and no cost.** `techdash.py` contains no LLM call. The
brief is written by Claude Code, reading a digest the dashboard exports:

```sh
~/techdash/techdash brief   # refreshes feeds, writes brief-input.md
# then, in Claude Code:
/brief
```

`/brief` (installed at `~/.claude/commands/brief.md`) runs the export, reads the
digest, and writes `cache/_brief.json`. Reload the page and it's there. Edit
that command file to change what the brief prioritizes — it holds the editorial
instructions, including what this reader cares about and what to skip.

The digest merges the same story across outlets, so a model release plus three
reaction pieces reaches Claude as one entry with four sources. Citations are
`[n]` numbers, not URLs — the server resolves them, so links can't be
mistranscribed. `--hours 48` widens the window; `--stale-ok` skips the refresh.

The brief never regenerates itself, so the page says when it has fallen behind:
past 6 hours the timestamp turns red and shows how many stories have landed
since; past 24 hours a banner appears above the brief with the rebuild command,
and the Brief tab gets a dot so you see it without switching tabs.

Because the digest is just a file, you can also ask ad-hoc questions in Claude
Code — *"read ~/techdash/brief-input.md, anything breaking in my stack?"* —
without any command at all.

## The page

| | |
|---|---|
| **Brief** | The written daily brief (above) — the landing tab |
| **Pulse** | HN front page, Show HN, Ask HN, Lobste.rs |
| **News** | Techmeme, TechCrunch, Verge, Ars, Register, InfoQ, WIRED, MIT TR, GitHub Changelog, Cloudflare, AWS, Chrome |
| **Market** | 16 tech tickers + Nasdaq, TechCrunch Venture, CNBC Tech |
| **Releases** | 50 GitHub release feeds: Go, CPython, Node, Deno, Bun, Rust, TypeScript, React, Next.js, React Native, Vite, Tailwind, shadcn/ui, TanStack, Vue, Svelte, Astro, uv, Ruff, FastAPI, Django, Postgres, Redis, DuckDB, Kubernetes, Ollama, llama.cpp, Claude Code, … |
| **New Libs** | PyPI (new + updates), crates.io, npm just-published, Go module index, RubyGems, GitHub rising repos, most-used npm |
| **AI & MCP** | MCP registry + spec + SDK releases, HN MCP/prompting/LLM chatter, OpenAI, DeepMind, Hugging Face, HF Daily Papers, arXiv cs.AI, Simon Willison, Latent Space |
| **Community** | Go/Python/Rust/PyTorch/HuggingFace/Kubernetes forums, JS/Node/React/Frontend/Rust/DB/Go weeklies, Pragmatic Engineer, Martin Fowler, Julia Evans, Netflix, Stripe, ACM Queue, dev.to, official Go/React/Next/Python/Node/Rust blogs (+ 10 subreddits if you add Reddit credentials) |

Every item carries a summary pulled from the feed's own description — about
three lines, cut at a word boundary. Roughly 75% of items have one; the
exceptions are link aggregators (HN, Lobste.rs) that publish titles only, where
the destination domain is shown instead.

Cards show their top 5 items with a `+N more` expander, so a tab is a screen or
two rather than a scroll marathon. Searching or picking a tag expands everything
automatically.

**Keyboard:** `/` search · `1`–`9` tabs · `r` refresh · `v` cards↔firehose ·
`t` light/dark · `Esc` clear filters.

Other bits: `#market`, `#releases` etc. deep-link a tab. **stable only** hides
canary/rc/beta builds. **new only** shows what landed since you last had the tab
open — click the counter to mark everything read. Tag chips (`go`, `react`,
`py`, `mcp`, …) stack with the search box. The footer expands into a per-source
health list.

## Adding a source

One line in `sources.py`. RSS and Atom need nothing else:

```python
Source("golangweekly", "Golang Weekly", "community",
       "https://golangweekly.com/rss", ttl=21600, tag="go")
```

A JSON API needs a loader — write a `load_x(source, get) -> [item]` in
`techdash.py` and register it in `LOADERS`, then set `kind="json", parser="x"`.

## Turning Reddit on (optional)

Reddit has closed anonymous access: `/r/x/.rss` returns 429 and `/r/x/.json`
returns 403 from any IP that isn't a signed-in browser, whatever User-Agent you
send. OAuth is the only route left, so the ten subreddit sources only load when
credentials are present — otherwise they don't exist, rather than sitting on the
Community tab as permanent error cards.

To enable them:

1. Go to <https://www.reddit.com/prefs/apps> → **create another app…**
2. Pick type **script**, put anything in the redirect URI (`http://localhost`).
3. Export the two values and restart:

```sh
export REDDIT_CLIENT_ID=<the id under the app name>
export REDDIT_CLIENT_SECRET=<the secret>
techdash
```

That grants 100 requests/minute, plenty for ten subreddits. Without it you lose
nothing important — the Discourse forums below cover the same ground.

> Note: the OAuth path is written against Reddit's documented
> client-credentials flow but has not been exercised here, since no Reddit app
> credentials existed on this machine. If it misbehaves, that's why.

## Known limits

- **Anthropic** publishes no RSS feed. Their news arrives via the `anthropics/*`
  release feeds, HN, and Simon Willison instead.
- **Quotes** come from Yahoo's chart endpoint — delayed, and it 429s if hit
  concurrently, so the 16 symbols are fetched one at a time over ~10s.
- **GitHub search** (the rising-repos card) is 10 requests/min unauthenticated.
  Export `GITHUB_TOKEN` to raise it; not required.

## Tests

```sh
techdash --no-open &     # the suite drives a running server
./tests/run.sh
```

130 checks: `tests/backend.py` covers the parsers, summary trimming, prerelease
detection, the brief export round-trip (including corrupt/missing files and an
empty cache), every HTTP route, and the CLI. `tests/ui.html` is driven by
headless Chrome against the real page — tabs, card collapse, search, tag chips,
the stable-only toggle, ticker tiles, firehose, keyboard shortcuts, deep links,
and the staleness states.

## Files

```
techdash.py      fetch engine, cache, scheduler, brief export, HTTP server
sources.py       the source registry — edit this to add feeds
static/          index.html, style.css, app.js
cache/           one JSON file per source; safe to delete
cache/_brief.json  the brief Claude Code wrote; delete to clear it
brief-input.md   the exported digest (regenerated each `techdash brief`)

tests/           backend.py, ui.html, run.sh

~/.claude/commands/brief.md   the /brief slash command + editorial instructions
```

## Desktop widget + menu bar app

`./widget/build.sh` (needs Xcode and `brew install xcodegen`) builds
`~/Applications/Techdash.app` and adds a login item (`--no-login` to skip).

- **Desktop widget "Tech Brief"**: right-click the desktop > Edit Widgets >
  search "Tech Brief". Small/medium/large/extra-large; shows the headline and
  items, click an item for its source, **Dashboard** and **New brief** buttons.
- **Menu bar icon**: the same brief as a menu, plus Write new brief (⌘B).

The widget is sandboxed, so its buttons are `techdash://open` / `techdash://write`
links handled by the app, which starts the server and runs `claude -p /brief`.
Logs: `cache/widget.log`. Remove: delete the app and
`~/Library/LaunchAgents/com.techdash.widget.plist`.
