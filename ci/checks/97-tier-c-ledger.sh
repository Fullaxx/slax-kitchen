#!/bin/sh
# stages: pre-commit pre-push ci
# desc: The Tier C ledger is well-formed and carries no facts about the host that ran it.
#
# tests/boot/tier-c.json is the only file in this repository written by a machine that is
# not CI -- a KVM host, because GitHub runners have no /dev/kvm and can never produce it.
# ci/release-notes.sh reads it, so a release's claim about Tier C is derived from it
# rather than hardcoded. That makes two things worth gating.
#
# ONE: shape. A closed key set with typed values, so a hand-edited or truncated ledger
# fails loudly instead of quietly making the release notes say less (or more) than
# happened.
#
# TWO, and the reason this gate exists at all: NOTHING ABOUT THE MACHINE. The repo's
# standing rule is that results are facts about the artifact -- an image name, a size, a
# marker, a duration -- while the host that produced them is nobody's business. The
# harness already records basenames rather than paths, but a convention nobody checks is
# not a guarantee, so `iso_name` must contain no separator and no string anywhere may
# look like a filesystem path or a home directory.
#
# Absent is fine and means Tier C has not been run; the release notes then say so.
. "$(dirname "$0")/../lib.sh"

LEDGER="$REPO_ROOT/tests/boot/tier-c.json"
[ -f "$LEDGER" ] || { note "tests/boot/tier-c.json not present - Tier C has not been run"; exit 0; }
have python3 || { note "python3 not installed - skipping the Tier C ledger check"; exit 0; }

python3 - "$LEDGER" <<'PY' || fail "tests/boot/tier-c.json"
import json, re, sys

DOC_KEYS = {"kitchen": str, "commit": str, "qemu": str, "accel": str,
            "date": str, "runs": list}
RUN_KEYS = {"accel": str, "golden": str, "iso_bytes": int, "iso_name": str,
            "markers": list, "missing": list, "path": str, "profile": str,
            "result": str, "screenshot_bytes": int, "seconds_ceiling": int,
            "target": str, "waited_s": (int, float), "run_tag": str,
            "busybox": str, "commit": str, "date": str}
REQUIRED_RUN = {"accel", "commit", "iso_bytes", "iso_name", "markers", "missing",
                "path", "profile", "result", "target"}
PATHS = {"bios", "uefi", "usb", "persistence", "kernel"}
RESULTS = {"pass", "fail"}
ACCELS = {"kvm", "tcg"}

# A value that names a place on somebody's disk. Checked on every string in the document,
# not only the ones we expect to be paths.
HOSTISH = re.compile(r"(^/|/home/|/root/|/Users/|~/|\\\\)")

bad = []
try:
    doc = json.load(open(sys.argv[1]))
except Exception as e:                             # noqa: BLE001
    print(f"  not valid JSON: {e}")
    raise SystemExit(1)

if not isinstance(doc, dict):
    print("  top level is not an object")
    raise SystemExit(1)

for k in DOC_KEYS:
    if k not in doc:
        bad.append(f"missing top-level key: {k}")
for k, v in doc.items():
    if k not in DOC_KEYS:
        bad.append(f"unknown top-level key: {k}")
    elif not isinstance(v, DOC_KEYS[k]):
        bad.append(f"{k} should be {DOC_KEYS[k].__name__}, got {type(v).__name__}")

if isinstance(doc.get("accel"), str) and doc["accel"] not in ACCELS:
    bad.append(f"accel: {doc['accel']!r} is not one of {sorted(ACCELS)}")
if isinstance(doc.get("date"), str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["date"]):
    bad.append(f"date: {doc['date']!r} is not YYYY-MM-DD")
if doc.get("commit") in ("", "unknown", None):
    bad.append("commit: a ledger that cannot say which tree it tested is not evidence")

for i, r in enumerate(doc.get("runs", [])):
    where = f"runs[{i}]"
    if not isinstance(r, dict):
        bad.append(f"{where} is not an object")
        continue
    for k in REQUIRED_RUN - set(r):
        bad.append(f"{where}: missing {k}")
    for k, v in r.items():
        if k not in RUN_KEYS:
            bad.append(f"{where}: unknown key {k!r}")
        elif not isinstance(v, RUN_KEYS[k]) or isinstance(v, bool):
            want = RUN_KEYS[k]
            name = want.__name__ if isinstance(want, type) else "number"
            bad.append(f"{where}.{k}: should be {name}, got {type(v).__name__}")
    if r.get("path") not in PATHS and "path" in r:
        bad.append(f"{where}.path: {r['path']!r} is not one of {sorted(PATHS)}")
    if r.get("result") not in RESULTS and "result" in r:
        bad.append(f"{where}.result: {r['result']!r} is not one of {sorted(RESULTS)}")
    if isinstance(r.get("iso_name"), str) and ("/" in r["iso_name"] or "\\" in r["iso_name"]):
        bad.append(f"{where}.iso_name: {r['iso_name']!r} is a path, not a name")
    if r.get("iso_bytes", 1) <= 0:
        bad.append(f"{where}.iso_bytes: should be a real size")
    if r.get("commit") in ("", "unknown"):
        bad.append(f"{where}.commit: a run that cannot name its tree is not evidence")

def walk(o, where):
    if isinstance(o, str):
        if HOSTISH.search(o):
            bad.append(f"{where}: {o!r} looks like a path on the machine that ran this")
    elif isinstance(o, dict):
        for k, v in o.items():
            walk(v, f"{where}.{k}")
    elif isinstance(o, list):
        for n, v in enumerate(o):
            walk(v, f"{where}[{n}]")

walk(doc, "ledger")

if bad:
    for b in bad:
        print(f"  {b}")
    raise SystemExit(1)
targets = sorted({r["target"] for r in doc["runs"]})
commits = sorted({r.get("commit", "?") for r in doc["runs"]})
print(f"  tier-c ledger: {len(doc['runs'])} runs over {len(targets)} target(s), "
      f"commit{'s' if len(commits) > 1 else ''} {', '.join(commits)}")
PY
check_result
