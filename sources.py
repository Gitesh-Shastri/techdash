"""The source registry: every feed techdash knows how to pull.

Adding a source is a one-line edit here. Nothing else in the codebase needs to
change -- techdash.py discovers everything through SOURCES.

Fields:
  id       stable slug; also the cache filename, so don't rename casually
  name     what shows on the card header
  section  which tab it lands in (see SECTIONS)
  url      what we fetch
  kind     "rss" (RSS or Atom, autodetected) or "json" (needs a parser)
  parser   for kind="json", the key into techdash.JSON_PARSERS
  ttl      seconds before the entry is considered stale and refetched
  tag      short chip on each item, used by the tag filter (go/react/py/ai/...)
  lane     "default" (parallel) or "polite" (serialized, throttled, backs off)
"""

import os
from dataclasses import dataclass

SECTIONS = [
    ("pulse", "Pulse", "What the whole industry is reading right now"),
    ("news", "News", "Tech press and industry coverage"),
    ("market", "Market", "Tickers, funding, and the business of tech"),
    ("releases", "Releases", "New versions of the things you actually build with"),
    ("libs", "New Libs", "Freshly published packages and rising repos"),
    ("ai", "AI & MCP", "Model releases, MCP ecosystem, prompting, papers"),
    ("community", "Community", "Forums, newsletters, and practitioner writing"),
]


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    section: str
    url: str
    kind: str = "rss"
    parser: str = ""
    ttl: int = 1800
    tag: str = ""
    lane: str = "default"
    limit: int = 20
    note: str = ""


# --------------------------------------------------------------------------
# Stack releases. (repo, display name, tag) -> one GitHub releases.atom source.
# This is the "what's new in Go / React / Next / Python" answer, and it beats a
# blog feed because it fires the moment a version is cut.
# --------------------------------------------------------------------------

STACK_REPOS = [
    # languages & runtimes
    ("golang/go", "Go", "go"),
    ("python/cpython", "CPython", "py"),
    ("nodejs/node", "Node.js", "js"),
    ("denoland/deno", "Deno", "js"),
    ("oven-sh/bun", "Bun", "js"),
    ("rust-lang/rust", "Rust", "rust"),
    ("microsoft/TypeScript", "TypeScript", "js"),
    # react / next / frontend
    ("facebook/react", "React", "react"),
    ("vercel/next.js", "Next.js", "react"),
    ("remix-run/react-router", "React Router", "react"),
    ("facebook/react-native", "React Native", "react"),
    ("vitejs/vite", "Vite", "js"),
    ("tailwindlabs/tailwindcss", "Tailwind", "css"),
    ("shadcn-ui/ui", "shadcn/ui", "react"),
    ("TanStack/query", "TanStack Query", "react"),
    ("vuejs/core", "Vue", "js"),
    ("sveltejs/kit", "SvelteKit", "js"),
    ("withastro/astro", "Astro", "js"),
    ("colinhacks/zod", "Zod", "js"),
    ("biomejs/biome", "Biome", "js"),
    ("vercel/turborepo", "Turborepo", "js"),
    # python
    ("astral-sh/uv", "uv", "py"),
    ("astral-sh/ruff", "Ruff", "py"),
    ("fastapi/fastapi", "FastAPI", "py"),
    ("django/django", "Django", "py"),
    ("pallets/flask", "Flask", "py"),
    ("pydantic/pydantic", "Pydantic", "py"),
    ("pandas-dev/pandas", "pandas", "py"),
    ("pytorch/pytorch", "PyTorch", "py"),
    ("huggingface/transformers", "Transformers", "py"),
    # go ecosystem
    ("gin-gonic/gin", "Gin", "go"),
    ("spf13/cobra", "Cobra", "go"),
    ("grpc/grpc-go", "gRPC-Go", "go"),
    # data & infra
    ("postgres/postgres", "PostgreSQL", "infra"),
    ("redis/redis", "Redis", "infra"),
    ("duckdb/duckdb", "DuckDB", "infra"),
    ("kubernetes/kubernetes", "Kubernetes", "infra"),
    ("docker/compose", "Docker Compose", "infra"),
    ("prisma/prisma", "Prisma", "js"),
    ("drizzle-team/drizzle-orm", "Drizzle", "js"),
    ("supabase/supabase", "Supabase", "infra"),
    ("tauri-apps/tauri", "Tauri", "rust"),
    # ai tooling
    ("ollama/ollama", "Ollama", "ai"),
    ("ggml-org/llama.cpp", "llama.cpp", "ai"),
    ("langchain-ai/langchain", "LangChain", "ai"),
    ("run-llama/llama_index", "LlamaIndex", "ai"),
    ("vllm-project/vllm", "vLLM", "ai"),
    ("anthropics/claude-code", "Claude Code", "ai"),
    ("anthropics/anthropic-sdk-python", "Anthropic SDK", "ai"),
    ("openai/openai-python", "OpenAI SDK", "ai"),
]

_RELEASES = [
    Source(
        id=f"rel-{repo.replace('/', '-').replace('.', '-').lower()}",
        name=label,
        section="releases",
        url=f"https://github.com/{repo}/releases.atom",
        tag=tag,
        ttl=3600,
        # Pull 8 so a card still has content after "stable only" strips the
        # rc/canary entries that some projects publish daily.
        limit=8,
    )
    for repo, label, tag in STACK_REPOS
]


# --------------------------------------------------------------------------
# Everything else, grouped by section.
# --------------------------------------------------------------------------

_FEEDS = [
    # ---- pulse -----------------------------------------------------------
    Source("hn-front", "Hacker News", "pulse",
           "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30",
           kind="json", parser="hn", ttl=600, tag="hn", limit=30),
    Source("hn-show", "Show HN", "pulse",
           "https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=20",
           kind="json", parser="hn", ttl=1800, tag="hn"),
    # Relevance-sorted Algolia queries need a {since:Nd} window, or they serve
    # the all-time greatest hits instead of what happened this week.
    Source("hn-ask", "Ask HN", "pulse",
           "https://hn.algolia.com/api/v1/search?tags=ask_hn&hitsPerPage=15"
           "&numericFilters=created_at_i%3E{since:4d}",
           kind="json", parser="hn", ttl=3600, tag="hn", limit=15),
    Source("lobsters", "Lobste.rs", "pulse", "https://lobste.rs/rss", ttl=1800, tag="lobsters"),

    # ---- news ------------------------------------------------------------
    Source("techmeme", "Techmeme", "news", "https://www.techmeme.com/feed.xml", ttl=900, tag="news", limit=25),
    Source("techcrunch", "TechCrunch", "news", "https://techcrunch.com/feed/", tag="news"),
    Source("verge", "The Verge", "news", "https://www.theverge.com/rss/index.xml", tag="news"),
    Source("ars", "Ars Technica", "news", "https://feeds.arstechnica.com/arstechnica/index", tag="news"),
    Source("register", "The Register", "news", "https://www.theregister.com/headlines.atom", tag="news"),
    Source("infoq", "InfoQ", "news", "https://feed.infoq.com/", ttl=3600, tag="eng"),
    Source("wired", "WIRED", "news", "https://www.wired.com/feed/rss", ttl=3600, tag="news"),
    Source("mittr", "MIT Tech Review", "news", "https://www.technologyreview.com/feed/", ttl=3600, tag="news"),
    Source("gh-changelog", "GitHub Changelog", "news", "https://github.blog/changelog/feed/", ttl=3600, tag="infra"),
    Source("cloudflare", "Cloudflare Blog", "news", "https://blog.cloudflare.com/rss/", ttl=3600, tag="infra"),
    Source("aws-new", "AWS What's New", "news",
           "https://aws.amazon.com/about-aws/whats-new/recent/feed/", ttl=3600, tag="infra"),
    Source("chrome-dev", "Chrome for Devs", "news",
           "https://developer.chrome.com/static/blog/feed.xml", ttl=7200, tag="web"),
    Source("google-dev", "Google Developers", "news",
           "https://developers.googleblog.com/feeds/posts/default", ttl=7200, tag="news"),

    # ---- market ----------------------------------------------------------
    Source("tickers", "Tech Tickers", "market", "", kind="json", parser="tickers", ttl=900, tag="market", limit=99),
    Source("tc-venture", "TechCrunch Venture", "market",
           "https://techcrunch.com/category/venture/feed/", ttl=1800, tag="funding"),
    Source("cnbc-tech", "CNBC Tech", "market",
           "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
           ttl=1800, tag="market"),

    # ---- new libs --------------------------------------------------------
    Source("gh-trending", "GitHub Rising", "libs", "", kind="json", parser="gh_trending", ttl=3600, tag="github", limit=25),
    Source("pypi-new", "PyPI — new packages", "libs", "https://pypi.org/rss/packages.xml", ttl=1800, tag="py", limit=25),
    Source("pypi-updates", "PyPI — updates", "libs", "https://pypi.org/rss/updates.xml", ttl=1800, tag="py", limit=25),
    Source("crates-new", "crates.io — new", "libs",
           "https://crates.io/api/v1/summary", kind="json", parser="crates", ttl=1800, tag="rust", limit=25),
    Source("npm-new", "npm — just published", "libs",
           "https://replicate.npmjs.com/_changes?descending=true&limit=60",
           kind="json", parser="npm", ttl=1800, tag="js", limit=30),
    Source("go-index", "Go module index", "libs", "", kind="json", parser="go_index", ttl=1800, tag="go", limit=25),
    Source("rubygems", "RubyGems — new", "libs",
           "https://rubygems.org/api/v1/activity/latest.json", kind="json", parser="rubygems", ttl=3600, tag="ruby", limit=15),
    Source("jsdelivr", "npm — most used (wk)", "libs",
           "https://data.jsdelivr.com/v1/stats/packages?period=week&type=npm&limit=25",
           kind="json", parser="jsdelivr", ttl=21600, tag="js", limit=25),

    # ---- ai & mcp --------------------------------------------------------
    Source("mcp-registry", "MCP Registry", "ai",
           "https://registry.modelcontextprotocol.io/v0/servers?limit=40",
           kind="json", parser="mcp_registry", ttl=3600, tag="mcp", limit=30),
    Source("mcp-spec", "MCP Spec releases", "ai",
           "https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom", ttl=7200, tag="mcp", limit=6),
    Source("mcp-servers", "MCP reference servers", "ai",
           "https://github.com/modelcontextprotocol/servers/commits/main.atom", ttl=7200, tag="mcp", limit=10),
    Source("mcp-py-sdk", "MCP Python SDK", "ai",
           "https://github.com/modelcontextprotocol/python-sdk/releases.atom", ttl=7200, tag="mcp", limit=6),
    Source("mcp-ts-sdk", "MCP TypeScript SDK", "ai",
           "https://github.com/modelcontextprotocol/typescript-sdk/releases.atom", ttl=7200, tag="mcp", limit=6),
    Source("hn-mcp", "HN — MCP chatter", "ai",
           "https://hn.algolia.com/api/v1/search_by_date?query=MCP&hitsPerPage=20",
           kind="json", parser="hn", ttl=3600, tag="mcp"),
    Source("hn-prompt", "HN — prompting", "ai",
           "https://hn.algolia.com/api/v1/search_by_date?query=prompt%20engineering&hitsPerPage=20",
           kind="json", parser="hn", ttl=7200, tag="prompt"),
    Source("hn-llm", "HN — LLMs", "ai",
           "https://hn.algolia.com/api/v1/search?query=LLM&tags=story&hitsPerPage=20"
           "&numericFilters=created_at_i%3E{since:5d}",
           kind="json", parser="hn", ttl=3600, tag="ai"),
    Source("openai-news", "OpenAI", "ai", "https://openai.com/news/rss.xml", ttl=7200, tag="ai", limit=10),
    Source("deepmind", "Google DeepMind", "ai", "https://deepmind.google/blog/rss.xml", ttl=7200, tag="ai", limit=10),
    Source("hf-blog", "Hugging Face Blog", "ai", "https://huggingface.co/blog/feed.xml", ttl=7200, tag="ai", limit=12),
    Source("hf-papers", "HF Daily Papers", "ai",
           "https://huggingface.co/api/daily_papers", kind="json", parser="hf_papers", ttl=21600, tag="papers", limit=15),
    Source("arxiv-ai", "arXiv cs.AI", "ai", "http://export.arxiv.org/rss/cs.AI", ttl=21600, tag="papers", limit=15),
    Source("simonw", "Simon Willison", "ai", "https://simonwillison.net/atom/everything/", ttl=3600, tag="prompt", limit=15),
    Source("latentspace", "Latent Space", "ai", "https://www.latent.space/feed", ttl=21600, tag="ai", limit=8),

    # ---- community -------------------------------------------------------
    Source("devto", "dev.to", "community", "https://dev.to/feed", ttl=3600, tag="devto"),

    # Discourse forums: the practitioner Q&A that the subreddits used to cover,
    # and unlike Reddit they serve RSS to anyone who asks.
    Source("go-forum", "Go Forum", "community",
           "https://forum.golangbridge.org/latest.rss", ttl=7200, tag="go", limit=12),
    Source("python-discuss", "Python Discuss", "community",
           "https://discuss.python.org/latest.rss", ttl=7200, tag="py", limit=12),
    Source("rust-users", "Rust Users Forum", "community",
           "https://users.rust-lang.org/latest.rss", ttl=7200, tag="rust", limit=12),
    Source("pytorch-forum", "PyTorch Forum", "community",
           "https://discuss.pytorch.org/latest.rss", ttl=7200, tag="ai", limit=12),
    Source("hf-forum", "Hugging Face Forum", "community",
           "https://discuss.huggingface.co/latest.rss", ttl=7200, tag="ai", limit=12),
    Source("k8s-discuss", "Kubernetes Discuss", "community",
           "https://discuss.kubernetes.io/latest.rss", ttl=7200, tag="infra", limit=12),

    # Weekly newsletters -- curated, low volume, high signal.
    Source("js-weekly", "JavaScript Weekly", "community",
           "https://javascriptweekly.com/rss", ttl=21600, tag="js", limit=5),
    Source("node-weekly", "Node Weekly", "community",
           "https://nodeweekly.com/rss", ttl=21600, tag="js", limit=5),
    Source("react-status", "React Status", "community",
           "https://react.statuscode.com/rss", ttl=21600, tag="react", limit=5),
    Source("frontend-focus", "Frontend Focus", "community",
           "https://frontendfoc.us/rss", ttl=21600, tag="web", limit=5),
    Source("this-week-rust", "This Week in Rust", "community",
           "https://this-week-in-rust.org/rss.xml", ttl=21600, tag="rust", limit=5),
    Source("db-weekly", "DB Weekly", "community",
           "https://dbweekly.com/rss", ttl=21600, tag="infra", limit=5),
    Source("console-dev", "Console (new dev tools)", "community",
           "https://console.dev/rss.xml", ttl=21600, tag="eng", limit=8),
    Source("golangweekly", "Golang Weekly", "community", "https://golangweekly.com/rss", ttl=21600, tag="go", limit=6),

    # Practitioner writing.
    Source("pragmatic-eng", "The Pragmatic Engineer", "community",
           "https://blog.pragmaticengineer.com/rss/", ttl=21600, tag="eng", limit=8),
    Source("so-blog", "Stack Overflow Blog", "community",
           "https://stackoverflow.blog/feed/", ttl=21600, tag="eng", limit=8),
    Source("martinfowler", "Martin Fowler", "community",
           "https://martinfowler.com/feed.atom", ttl=43200, tag="eng", limit=8),
    Source("julia-evans", "Julia Evans", "community",
           "https://jvns.ca/atom.xml", ttl=43200, tag="eng", limit=6),
    Source("netflix-tech", "Netflix Tech Blog", "community",
           "https://netflixtechblog.com/feed", ttl=43200, tag="eng", limit=6),
    Source("stripe-blog", "Stripe Engineering", "community",
           "https://stripe.com/blog/feed.rss", ttl=43200, tag="eng", limit=6),
    Source("acm-queue", "ACM Queue", "community",
           "https://queue.acm.org/rss/feeds/queuecontent.xml", ttl=43200, tag="eng", limit=8),
    Source("gh-blog", "GitHub Blog", "community", "https://github.blog/feed/", ttl=7200, tag="eng", limit=10),
    Source("vercel-blog", "Vercel Blog", "community", "https://vercel.com/atom", ttl=21600, tag="react", limit=8),
    Source("go-blog", "The Go Blog", "community", "https://go.dev/blog/feed.atom", ttl=21600, tag="go", limit=8),
    Source("react-blog", "React Blog", "community", "https://react.dev/rss.xml", ttl=21600, tag="react", limit=8),
    Source("next-blog", "Next.js Blog", "community", "https://nextjs.org/feed.xml", ttl=21600, tag="react", limit=8),
    Source("python-insider", "Python Insider", "community",
           "https://blog.python.org/feeds/posts/default", ttl=21600, tag="py", limit=8),
    Source("node-blog", "Node.js Blog", "community", "https://nodejs.org/en/feed/blog.xml", ttl=21600, tag="js", limit=8),
    Source("rust-blog", "Rust Blog", "community", "https://blog.rust-lang.org/feed.xml", ttl=21600, tag="rust", limit=8),
]

# Reddit killed anonymous access: .rss returns 429 and .json returns 403 from
# any IP that isn't a logged-in browser, no matter the User-Agent. The only
# route left is OAuth, so these only exist once credentials are present --
# better an absent tab than ten cards permanently reading "HTTP 429".
# See README "Turning Reddit on".
REDDIT_ENABLED = bool(os.environ.get("REDDIT_CLIENT_ID")
                      and os.environ.get("REDDIT_CLIENT_SECRET"))

_REDDIT = [
    Source(f"r-{sub.lower()}", f"r/{sub}", "community",
           f"https://oauth.reddit.com/r/{sub}/hot?limit=15&raw_json=1",
           kind="json", parser="reddit", ttl=1800, tag=tag, limit=12)
    for sub, tag in [
        ("programming", "eng"), ("golang", "go"), ("reactjs", "react"),
        ("nextjs", "react"), ("Python", "py"), ("rust", "rust"),
        ("LocalLLaMA", "ai"), ("MachineLearning", "ai"), ("devops", "infra"),
        ("ExperiencedDevs", "eng"),
    ]
] if REDDIT_ENABLED else []

SOURCES = _FEEDS + _RELEASES + _REDDIT

BY_ID = {s.id: s for s in SOURCES}

# Tickers pulled for the Market tab.
TICKERS = [
    ("NVDA", "Nvidia"), ("MSFT", "Microsoft"), ("AAPL", "Apple"),
    ("GOOGL", "Alphabet"), ("AMZN", "Amazon"), ("META", "Meta"),
    ("TSLA", "Tesla"), ("AVGO", "Broadcom"), ("AMD", "AMD"),
    ("ORCL", "Oracle"), ("CRM", "Salesforce"), ("PLTR", "Palantir"),
    ("NET", "Cloudflare"), ("SNOW", "Snowflake"), ("MDB", "MongoDB"),
    ("^IXIC", "Nasdaq"),
]

# GitHub reports full language names; fold them into the tag vocabulary the
# chips already use so filtering by "js" catches TypeScript repos too.
LANGUAGE_TAGS = {
    "typescript": "js", "javascript": "js", "vue": "js", "svelte": "js",
    "python": "py", "jupyter notebook": "py",
    "go": "go", "rust": "rust", "ruby": "ruby",
    "c++": "infra", "c": "infra", "shell": "infra", "dockerfile": "infra",
    "html": "web", "css": "css", "scss": "css",
    "java": "eng", "kotlin": "eng", "swift": "eng", "c#": "eng",
}

# GitHub search queries for the "rising repos" card.
TRENDING_QUERIES = [
    ("", "any"),
    ("language:go", "go"),
    ("language:typescript", "js"),
    ("language:python", "py"),
    ("language:rust", "rust"),
]

assert len(BY_ID) == len(SOURCES), "duplicate source id in registry"
