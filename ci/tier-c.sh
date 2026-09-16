#!/bin/sh
# Tier C: run every boot path against one image, and record what happened.
#
#   ci/tier-c.sh [options]
#
#   --iso FILE         image to boot   (default: the one `--profile` builds)
#   --profile NAME     profile whose output to use   (default: boot-matrix)
#   --target NAME      label recorded in the ledger  (default: from the profile base)
#   --out DIR          where evidence lands          (default: out/tier-c)
#   --ledger FILE      the committed record    (default: tests/boot/tier-c.json)
#   --golden-dir DIR   committed testkit blocks (default: tests/boot/golden)
#   --seconds N        ceiling per boot               (default: 120)
#   --paths "a b c"    subset of: bios uefi usb persistence
#   --keep             keep scratch artifacts instead of cleaning up
#
# WHAT THIS IS FOR. CI has no /dev/kvm, so it can exercise this harness but can never
# be the evidence. This runs on any KVM-capable Linux host with qemu, qemu-img, xorriso,
# e2fsprogs and OVMF, and writes back two things that ARE committable, because both are
# facts about the artifact rather than about the machine that booted it:
#
#   the ledger   one row per boot: which path, image name and size, which markers were
#                seen, how long it took. ci/release-notes.sh reads it, so the release
#                claim about Tier C stops being a hardcoded sentence.
#   the goldens  the testkit block each boot produced. ONE golden per image, not per
#                path, which asserts something stronger than a regression: every boot
#                path must assemble an identical filesystem. A GRUB cmdline that drifts
#                from the isolinux one shows up here and nowhere else.
#
# NOTHING HOST-SPECIFIC GOES IN EITHER. No hostname, no path, no user. The ledger gate
# (ci/checks/97-tier-c-ledger.sh) enforces that rather than trusting this script.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT" || exit 2

ISO="" PROFILE=boot-matrix TARGET="" OUT=out/tier-c
LEDGER=tests/boot/tier-c.json GOLDEN_DIR=tests/boot/golden
SECONDS_CEIL=120 PATHS="bios uefi usb persistence" KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --iso)        ISO=$2; shift 2 ;;
        --profile)    PROFILE=$2; shift 2 ;;
        --target)     TARGET=$2; shift 2 ;;
        --out)        OUT=$2; shift 2 ;;
        --ledger)     LEDGER=$2; shift 2 ;;
        --golden-dir) GOLDEN_DIR=$2; shift 2 ;;
        --seconds)    SECONDS_CEIL=$2; shift 2 ;;
        --paths)      PATHS=$2; shift 2 ;;
        --keep)       KEEP=1; shift ;;
        -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "tier-c.sh: unknown option $1" >&2; exit 2 ;;
    esac
done

R='\033[31m'; G='\033[32m'; Y='\033[33m'; B='\033[1m'; D='\033[2m'; O='\033[0m'
[ -t 1 ] || { R=''; G=''; Y=''; B=''; D=''; O=''; }
say()  { printf "%b\n" "$*"; }
die()  { printf "%btier-c.sh: %s%b\n" "$R" "$*" "$O" >&2; exit 2; }
have() { command -v "$1" >/dev/null 2>&1; }

# /usr/sbin is not always on PATH -- a non-login ssh session is the usual way to find
# that out, and it hides mkfs.ext4 rather than reporting it missing.
case ":$PATH:" in *:/usr/sbin:*) ;; *) PATH="$PATH:/usr/sbin:/sbin" ;; esac
export PATH
for t in qemu-system-x86_64 xorriso python3 mkfs.ext4; do
    have "$t" || die "$t not installed -- see docs/60-testing/qemu.md for the package list"
done
if [ -w /dev/kvm ]; then ACCEL=KVM; else
    ACCEL=TCG
    say "${Y}no writable /dev/kvm: every boot below runs under TCG, 10-20x slower.${O}"
    say "${Y}That is fine for checking the harness still works and wrong for producing${O}"
    say "${Y}evidence -- a ledger row saying accel=tcg is what tells them apart.${O}"
fi

# Resolve the image from the profile when not given one outright.
if [ -z "$ISO" ]; then
    ISO=$(python3 - "$PROFILE" <<'PY'
import sys, os, glob
sys.path.insert(0, "lib")
name = sys.argv[1]
p = name if os.path.isfile(name) else f"profiles/{name}.yaml"
import yaml
d = yaml.safe_load(open(p))
b = d["base"]
out = d.get("output", {}).get("name", "")
for k, v in (("{{version}}", b["version"]), ("{{flavour}}", b["flavour"]),
             ("{{arch}}", b["arch"]), ("{{name}}", d["metadata"]["name"])):
    out = out.replace(k, str(v))
print(os.path.join("out", out))
PY
) || die "could not resolve an ISO from profile $PROFILE"
fi
[ -f "$ISO" ] || die "no such image: $ISO
  build it first:  ./kitchen build $PROFILE"

if [ -z "$TARGET" ]; then
    TARGET=$(python3 - "$PROFILE" <<'PY'
import sys, os, yaml
name = sys.argv[1]
p = name if os.path.isfile(name) else f"profiles/{name}.yaml"
b = yaml.safe_load(open(p))["base"]
print(f'{b["flavour"]}-{b["arch"]}-{b["version"]}')
PY
) || die "could not resolve a target from profile $PROFILE"
fi

GOLDEN="$GOLDEN_DIR/$PROFILE-$TARGET.testkit"
mkdir -p "$OUT" "$GOLDEN_DIR" || die "cannot write to $OUT or $GOLDEN_DIR"
JSONL="$OUT/runs.jsonl"
: > "$JSONL"

# ------------------------------------------------------------------ cleanup ----
# The one artifact that must SURVIVE a boot is the persistence disk, because the whole
# test is that a second boot sees what the first wrote. So it is scoped to this run's
# directory and removed here rather than by the harness, which is handed the path and
# has no business deleting a caller's file.
#
# The trap matters more than it looks: this is normally driven over ssh, and a dropped
# connection would otherwise leave a multi-gigabyte qcow and a live qemu behind.
PIDDIR="$OUT/pids"   # written by lib/build.sh, one per boot
mkdir -p "$PIDDIR"
cleanup() {
    for pf in "$PIDDIR"/*.pid; do
        [ -e "$pf" ] || continue
        pid=$(cat "$pf" 2>/dev/null) || continue
        # Only pids THIS run wrote. Never pkill qemu: on a shared host that takes out
        # somebody else's virtual machines.
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
        rm -f "$pf"
    done
    [ "$KEEP" = 1 ] || rm -rf "$PIDDIR"
}
trap 'cleanup' EXIT INT TERM

# ------------------------------------------------------- what is here already ----
QB_BEFORE=$(ls -d /tmp/qb-* 2>/dev/null | wc -l | tr -d ' ')
OUT_BEFORE=$(du -sk out 2>/dev/null | cut -f1)

say ""
say "${B}Tier C boot matrix${O}"
say "  image    $(basename "$ISO") ($(( $(stat -c%s "$ISO") / 1048576 )) MiB)"
say "  target   $TARGET"
say "  paths    $PATHS"
say "  accel    $ACCEL"
say "  golden   $GOLDEN"
say ""

rc=0
for path in $PATHS; do
    case "$path" in
        bios|uefi|usb) flag="--$path" ;;
        persistence)   flag="--persistence" ;;
        kernel)        flag="--kernel" ;;
        *) die "unknown path: $path (want bios, uefi, usb, persistence or kernel)" ;;
    esac
    ./kitchen test "$ISO" "$flag" --out "$OUT" --seconds "$SECONDS_CEIL" \
        --golden "$GOLDEN" --record "$JSONL" || rc=1
done

# ------------------------------------------------------------------- ledger ----
python3 - "$JSONL" "$LEDGER" "$TARGET" "$PROFILE" "$ACCEL" <<'PY' || rc=1
import json, os, subprocess, sys, datetime
jsonl, ledger, target, profile, accel = sys.argv[1:6]

def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True).stdout.strip()
    except Exception:                              # noqa: BLE001
        return ""

rows = []
with open(jsonl) as f:
    for line in f:
        line = line.strip()
        if line:
            r = json.loads(line)
            r["target"] = target
            r["profile"] = profile
            rows.append(r)   # commit/date stamped below, once we know them
if not rows:
    print("  no runs recorded -- nothing to write", file=sys.stderr)
    raise SystemExit(1)

qemu = sh("qemu-system-x86_64", "--version").splitlines()
qemu = qemu[0].split()[3] if qemu and len(qemu[0].split()) > 3 else "unknown"
# A ledger that cannot say WHICH tree it tested is not evidence, it is a rumour. git
# refuses a repository owned by another user ("dubious ownership"), which is exactly the
# case when a host account boots images built inside a root-owned container clone -- and
# the first run of this script recorded commit "unknown" without complaining. Same shape
# as the vacuous-pass bug in ci/lib.sh: an empty result swallowed and treated as fine.
commit = sh("git", "describe", "--always", "--dirty")
if not commit:
    print("  refusing to write the ledger: `git describe` produced nothing.\n"
          "  Usually 'dubious ownership' -- git will not read a repository owned by\n"
          "  another user. Fix it for this tree and run again:\n"
          f"    git config --global --add safe.directory {os.getcwd()}",
          file=sys.stderr)
    raise SystemExit(1)

# MERGE BY TARGET, do not overwrite. Four targets are four invocations, and the first
# version of this wrote the file outright -- so a four-target sweep ended with a ledger
# describing only whichever target ran last, which is exactly the kind of quiet
# under-reporting the ledger exists to prevent.
#
# Replacing a target wholesale rather than appending to it: a re-run of one target must
# supersede its previous rows, not accumulate a history of them. The ledger says what is
# true now, and git says what was true before.
kept = []
if os.path.isfile(ledger):
    try:
        prev = json.load(open(ledger))
        kept = [r for r in prev.get("runs", []) if r.get("target") != target]
    except Exception as e:                         # noqa: BLE001
        print(f"  refusing to merge into an unreadable ledger: {e}", file=sys.stderr)
        raise SystemExit(1)

# Per row, because merging makes the top-level commit a claim about only the LAST target
# run. Four targets done across two days at two commits is a legitimate thing to have
# done, and a ledger that reports one commit for all of it is not describing it.
for r in rows:
    r["commit"] = commit
    r["date"] = datetime.date.today().isoformat()

merged = kept + rows
doc = {
    "kitchen": sh("./kitchen", "version").replace("kitchen ", "").split()[0] or "unknown",
    "commit": commit,
    "qemu": qemu,
    "accel": accel.lower(),
    "date": datetime.date.today().isoformat(),
    "runs": sorted(merged, key=lambda r: (r["target"], r["path"], r.get("run_tag", ""))),
}
os.makedirs(os.path.dirname(ledger) or ".", exist_ok=True)
with open(ledger, "w") as f:
    json.dump(doc, f, indent=2, sort_keys=True)
    f.write("\n")
covered = sorted({r["target"] for r in merged})
print(f"  ledger   {ledger} ({len(rows)} new, {len(merged)} total, "
      f"{len(covered)} target(s): {', '.join(covered)})")
PY

# -------------------------------------------------------------- leak check ----
# A cleanup nobody checks is the same defect as a check that cannot fail. Both numbers
# below were real leaks before this pass: 27 stale /tmp/qb-* directories in the dev
# container and 7 on the build host, and a 540 KB OVMF variables copy per UEFI run.
# NOT on a failure. kitchen test deliberately keeps a failed persistence disk and prints
# where it is, because then it IS the evidence -- and this line used to delete it two
# seconds later, which would have made the one artifact worth having the one artifact
# that never survived.
if [ "$KEEP" != 1 ] && [ "$rc" = 0 ]; then
    rm -rf "$OUT/perch"
fi
QB_AFTER=$(ls -d /tmp/qb-* 2>/dev/null | wc -l | tr -d ' ')
say ""
if [ "$QB_AFTER" -gt "$QB_BEFORE" ]; then
    say "${R}LEAK${O} /tmp/qb-* went from $QB_BEFORE to $QB_AFTER: the harness left"
    say "     qmp socket directories behind."
    rc=1
else
    say "${G}clean${O} no qmp socket directories leaked (still $QB_AFTER)"
fi
if [ "$KEEP" = 1 ]; then
    say "${Y}kept${O}  $OUT (--keep)"
elif [ "$rc" != 0 ]; then
    # A failed run keeps its persistence disk on purpose, so only the firmware scratch --
    # which is never evidence -- is a leak here.
    leftover=$(find "$OUT" -name '*.vars.fd' 2>/dev/null | wc -l | tr -d ' ')
    [ "$leftover" -gt 0 ] && say "${R}LEAK${O} $leftover OVMF variables copy left under $OUT"
    [ -e "$OUT/perch" ] && say "${Y}kept${O}  $OUT/perch -- a failed persistence run's disk is the evidence"
else
    leftover=$(find "$OUT" \( -name '*.vars.fd' -o -name 'perch.img' \) 2>/dev/null | wc -l | tr -d ' ')
    if [ "$leftover" -gt 0 ]; then
        say "${R}LEAK${O} $leftover scratch file(s) left under $OUT"
        find "$OUT" \( -name '*.vars.fd' -o -name 'perch.img' \) 2>/dev/null | sed 's/^/       /'
        rc=1
    else
        say "${G}clean${O} no firmware or persistence scratch left behind"
    fi
fi
OUT_AFTER=$(du -sk out 2>/dev/null | cut -f1)
say "${D}out/ grew by $(( ${OUT_AFTER:-0} - ${OUT_BEFORE:-0} )) KiB (evidence: serial logs and screenshots)${O}"

say ""
if [ "$rc" = 0 ]; then
    say "${G}tier-c ok${O}  every path booted and matched the golden"
else
    say "${R}tier-c failed${O}  see above; the ledger records what did run"
fi
exit $rc
