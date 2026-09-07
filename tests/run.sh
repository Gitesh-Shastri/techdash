#!/bin/sh
# techdash test suite. Requires the server running on :8787.
#   ./tests/run.sh
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

curl -sf -o /dev/null http://127.0.0.1:8787/ || { echo "start the server first: techdash --no-open &"; exit 1; }

echo "=== backend ==="
python3 "$HERE/tests/backend.py" || BACKEND_FAILED=1

echo
echo "=== frontend (headless Chrome drives the real page) ==="
# The harness must be same-origin with the app, so serve it from static/ briefly.
cp "$HERE/tests/ui.html" "$HERE/static/_test.html"
trap 'rm -f "$HERE/static/_test.html"' EXIT
"$CHROME" --headless --disable-gpu --virtual-time-budget=60000 \
  --dump-dom "http://127.0.0.1:8787/static/_test.html" 2>/dev/null | python3 -c "
import sys, re, html
dom = sys.stdin.read()
title = re.search(r'<title>(.*?)</title>', dom)
body = re.search(r'<pre id=\"out\">(.*?)</pre>', dom, re.S)
out = html.unescape(body.group(1)) if body else 'NO OUTPUT'
print(out)
print()
print('RESULT:', title.group(1) if title else '?')
sys.exit(0 if title and 'DONE' in title.group(1) and 'FAIL' not in out else 1)
"
[ -z "$BACKEND_FAILED" ] || { echo; echo "backend suite had failures"; exit 1; }
