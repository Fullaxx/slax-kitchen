#!/bin/bash
# Detect upstream movement: a new Slax release, a linux-live commit, or a dead mirror.
#
#   ci/upstream-watch.sh [--baseline compat/upstream-baseline.yaml]
#
# Exit 0 = nothing changed. Exit 1 = something moved and a human should look.
#
# Upstream has been dormant since 2023-10-10 and mirrors rot silently (slackonly.com
# went NXDOMAIN and broke slackpkg on every stock Slax). This is how we find out.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
BASELINE="$REPO_ROOT/compat/upstream-baseline.yaml"
[ "${1:-}" = "--baseline" ] && BASELINE=$2

changed=0
say()  { printf '  %s\n' "$*"; }
flag() { printf '  CHANGED: %s\n' "$*"; changed=1; }

want_head=$(sed -n 's/^ *linux_live_head: *//p' "$BASELINE" | tr -d '"')

echo "upstream watch"

# --- linux-live HEAD -------------------------------------------------------
got_head=$(git ls-remote https://github.com/Tomas-M/linux-live HEAD 2>/dev/null | cut -f1)
if [ -z "$got_head" ]; then
    say "linux-live: could not reach github (skipping)"
elif [ "$got_head" = "$want_head" ]; then
    say "linux-live HEAD unchanged ($(echo "$want_head" | cut -c1-12))"
else
    flag "linux-live HEAD $(echo "$want_head" | cut -c1-12) -> $(echo "$got_head" | cut -c1-12)"
    say "  review: https://github.com/Tomas-M/linux-live/compare/$want_head...$got_head"
fi

# --- Slax releases ---------------------------------------------------------
# The changelog page is the authoritative list; the mirror directory listing is a
# cheaper cross-check that also catches a release published without a changelog entry.
listing=$(curl -sS --max-time 30 https://ftp.linux.cz/pub/linux/slax/ 2>/dev/null)
if [ -z "$listing" ]; then
    say "mirror listing: unreachable (skipping release check)"
else
    trees=$(echo "$listing" | grep -oE 'href="Slax-[^"]+/"' | sed 's/href="//;s|/"||' | sort -u)
    say "mirror trees: $(echo "$trees" | tr '\n' ' ')"
    # Slax publishes two current releases, one per flavour, on the same day, and the
    # changelog lists the FULL history -- so neither "the newest entry" nor set
    # membership works. Compare per release line (major version): flag only a version
    # higher than the baseline for that line, or a line we have never seen.
    curl -sS --max-time 30 https://www.slax.org/changelog.php 2>/dev/null \
        | grep -oE 'Slax [0-9]+\.[0-9]+\.[0-9]+' | awk '{print $2}' | sort -u > /tmp/.slax-seen.$$
    if [ ! -s /tmp/.slax-seen.$$ ]; then
        say "changelog: unreachable or unparseable (skipping)"
    else
        python3 - "$BASELINE" /tmp/.slax-seen.$$ <<'PY' || changed=1
import sys, yaml
base = yaml.safe_load(open(sys.argv[1]))
known = [tuple(int(x) for x in v.split(".")) for v in base["latest_releases"]]
best = {}
for v in known:
    best[v[0]] = max(best.get(v[0], v), v)
# The changelog carries the FULL history back to 9.x, so an unseen major that is
# LOWER than anything we track is history, not news. Only two things matter: a
# higher point release on a line we track, or an entirely higher release line.
top = max(best)
news = []
for line in open(sys.argv[2]):
    line = line.strip()
    if not line:
        continue
    v = tuple(int(x) for x in line.split("."))
    if v[0] > top:
        news.append(f"{line} (new release line {v[0]}.x)")
    elif v[0] in best and v > best[v[0]]:
        news.append(f"{line} (baseline for {v[0]}.x is "
                    f"{'.'.join(map(str, best[v[0]]))})")
if news:
    print("  CHANGED: newer release(s) on slax.org: " + ", ".join(news))
    sys.exit(1)
print("  releases unchanged (" +
      ", ".join(".".join(map(str, v)) for v in sorted(best.values())) + " are still newest)")
PY
    fi
    rm -f /tmp/.slax-seen.$$
fi

# --- mirror health ---------------------------------------------------------
# A base ISO nobody can download is as broken as a bad checksum.
python3 - "$REPO_ROOT/compat/sources.yaml" <<'PY' || changed=1
import subprocess, sys, yaml
src = yaml.safe_load(open(sys.argv[1]))
bad = 0
for name, spec in sorted(src["targets"].items()):
    for m in src["mirrors"]:
        url = m["base"] + "/" + m["layout"].format(**spec)
        r = subprocess.run(["curl", "-sSIL", "--max-time", "25", url],
                           capture_output=True, text=True)
        code = [l.split()[1] for l in r.stdout.splitlines() if l.startswith("HTTP/")]
        size = [l.split()[1].strip() for l in r.stdout.splitlines()
                if l.lower().startswith("content-length:")]
        ok = code and code[-1] == "200" and size and int(size[-1]) == spec["size"]
        if ok:
            print(f"  ok       {name} @ {m['name']}")
        else:
            print(f"  CHANGED: {name} @ {m['name']} -> HTTP {code[-1] if code else '?'}"
                  f" size {size[-1] if size else '?'} (want {spec['size']})")
            bad += 1
sys.exit(1 if bad else 0)
PY

# --- pinned signing keys ---------------------------------------------------
# A recipe using apt.sources pins its vendor's signing key by sha256, so a rotation makes
# the build fail by design -- correctly, because an unpinned key lets a remote party
# decide what the image trusts. The failure is right; discovering it from a ten-minute
# build that happens to run is not. A few HTTP fetches and a sha256 find it in seconds.
#
# Parsed out of the recipes rather than hardcoded, so a recipe added later is covered
# without anyone remembering to update this.
python3 - "$REPO_ROOT/recipes" <<'PY' || changed=1
import hashlib, pathlib, subprocess, sys, yaml

pins, bad = [], 0
for f in sorted(pathlib.Path(sys.argv[1]).rglob("*.yaml")):
    try:
        doc = yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError:
        continue
    if not isinstance(doc, dict):
        continue
    for step in doc.get("steps") or []:
        for src in ((step.get("apt") or {}).get("sources") or []):
            if src.get("key_url") and src.get("key_sha256"):
                pins.append((f.stem, src.get("name", "?"), src["key_url"], src["key_sha256"]))

if not pins:
    print("  no pinned signing keys to check")
    sys.exit(0)

for recipe, name, url, want in pins:
    r = subprocess.run(["curl", "-sSL", "--max-time", "30", url], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        print(f"  CHANGED: {recipe}/{name} signing key unreachable -> {url}")
        bad += 1
        continue
    got = hashlib.sha256(r.stdout).hexdigest()
    if got == want:
        print(f"  ok       {recipe}/{name} key {got[:12]}... ({len(r.stdout)} bytes)")
    else:
        print(f"  CHANGED: {recipe}/{name} signing key ROTATED -> {url}")
        print(f"           want {want}")
        print(f"           got  {got}")
        bad += 1
sys.exit(1 if bad else 0)
PY

echo
if [ "$changed" -eq 0 ]; then
    echo "nothing changed"
else
    echo "upstream moved -- see docs/70-compat/ for the adoption procedure"
fi
exit "$changed"
