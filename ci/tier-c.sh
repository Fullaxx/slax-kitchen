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
#   --allow-dirty      run against a modified tree (the ledger is then not evidence)
#   --local            boot here, ignoring boot-host.ini
#
# WHAT THIS IS FOR. CI has no /dev/kvm, so it can exercise this harness but can never
# be the evidence. This runs on any KVM-capable Linux host with qemu, qemu-img, xorriso,
# e2fsprogs and OVMF, and writes back two things that ARE committable, because both are
# facts about the artifact rather than about the machine that booted it:
#
#   the ledger   one row per boot: which path, image name and size, which markers were
#                seen, how long it took, and the accelerator and qemu version it booted
#                under -- what it ran on, never which machine. ci/release-notes.sh reads
#                it, so the release claim about Tier C stops being a hardcoded sentence.
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
SECONDS_CEIL=120 PATHS="bios uefi usb persistence" KEEP=0 ALLOW_DIRTY=0
# TWO destinations, tracked separately, because --allow-dirty has to move BOTH. One flag
# cleared by either option is an OR, and the refusal below reads as an AND -- it names both
# options and says "as well", and docs/60-testing/tier-c.md says "and". Issue #18: with one
# flag, `--allow-dirty --ledger /tmp/x.json` passed while GOLDEN_DIR stayed at the committed
# default, and a missing golden is CREATED rather than failed, so a dirty exploratory run
# could write a golden into tests/boot/golden/ -- the hole 142fc21 closed, by the other door.
LEDGER_REDIRECTED=0 GOLDEN_REDIRECTED=0
while [ $# -gt 0 ]; do
    case "$1" in
        --iso)        ISO=$2; shift 2 ;;
        --profile)    PROFILE=$2; shift 2 ;;
        --target)     TARGET=$2; shift 2 ;;
        --out)        OUT=$2; shift 2 ;;
        --ledger)     LEDGER=$2; LEDGER_REDIRECTED=1; shift 2 ;;
        --golden-dir) GOLDEN_DIR=$2; GOLDEN_REDIRECTED=1; shift 2 ;;
        --seconds)    SECONDS_CEIL=$2; shift 2 ;;
        --paths)      PATHS=$2; shift 2 ;;
        --keep)       KEEP=1; shift ;;
        --allow-dirty) ALLOW_DIRTY=1; shift ;;
        # Reaches the boots through kitchen test, which is the one place that decides
        # where a boot runs. Exported, so the whole sweep agrees.
        --local)      KITCHEN_BOOT_HOST=local; export KITCHEN_BOOT_HOST; shift ;;
        -h|--help)    sed -n '2,31p' "$0"; exit 0 ;;
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
# WHAT IS NEEDED HERE DEPENDS ON WHERE THE BOOTS HAPPEN. With a boot host configured,
# qemu and mkfs.ext4 are checked on that machine by its own pre-run check, and demanding
# them here would refuse a sweep that does not use them -- on the dev container, every
# sweep. `boot_host.py active` answers 0 configured, 1 boots stay here, 2 the file is
# broken and has already been explained.
BOOT_HOST=""
_bh_rc=1
if [ -f boot-host.ini ] && [ -f lib/boot_host.py ]; then
    BOOT_HOST=$(python3 lib/boot_host.py active)
    _bh_rc=$?
fi
# NOT `|| true`: that resets $? to 0 and the refusal below could never fire.
[ "$_bh_rc" = 2 ] && exit 2
[ "$_bh_rc" = 0 ] || BOOT_HOST=""
if [ -n "$BOOT_HOST" ]; then
    NEED="python3 ssh rsync git"
else
    NEED="qemu-system-x86_64 xorriso python3 mkfs.ext4"
fi
for t in $NEED; do
    have "$t" || die "$t not installed -- see docs/60-testing/qemu.md for the package list"
done

# A RECORDED RUN MUST NAME A TREE SOMEBODY CAN CHECK OUT AGAIN. The ledger stamp is read
# from the working tree (see `git describe` below), and it is read when the ledger is
# WRITTEN, not now -- so a tracked file edited while a sweep is in flight taints every
# target that finishes afterwards, however unrelated the file. That happened twice while
# producing the four-target evidence, and nothing caught it either time: the ledger gate
# accepts a "-dirty" stamp, so the evidence quietly stops being reconstructible instead
# of failing. Refusing here costs a second; noticing afterwards cost two full re-runs.
if have git; then
    _tree=$(git describe --always --dirty 2>/dev/null || true)
    case "$_tree" in
        *-dirty)
            [ "$ALLOW_DIRTY" = 1 ] || die "the working tree is modified, so the
  ledger would be stamped '$_tree' -- a tree nobody can check out. Commit or stash
  first, or pass --allow-dirty for a run whose ledger you will not commit."
            # ...and then actually keep it out of the committed ledger. Writing there is
            # the default, so --allow-dirty on its own re-opens exactly the hole the
            # refusal above closes: the merge is by target, so one throwaway TCG boot
            # REPLACES that target's real rows and drags the document's accel down with
            # it. Found by running this demo against the defaults and watching 5 KVM rows
            # become 1 dirty TCG row.
            # BOTH, not either: a golden is written into GOLDEN_DIR the first time a
            # target is seen, so redirecting only the ledger still lets a dirty run put a
            # golden in the committed directory.
            [ "$LEDGER_REDIRECTED" = 1 ] && [ "$GOLDEN_REDIRECTED" = 1 ] || \
                die "--allow-dirty would write into the committed evidence
  (ledger $LEDGER, goldens $GOLDEN_DIR). Send BOTH somewhere else:
    --ledger /tmp/scratch.json --golden-dir /tmp/scratch-golden" ;;
    esac
fi
if [ -n "$BOOT_HOST" ]; then
    # THIS MACHINE'S /dev/kvm SAYS NOTHING ABOUT THE BOOTS. They happen on the boot
    # host, which refuses to run without a writable /dev/kvm of its own, so reading the
    # local one here would print "every boot below runs under TCG" over a sweep that is
    # about to run entirely under KVM somewhere else. The banner says where it will be
    # decided; the ledger rows still carry what each boot actually got.
    ACCEL="on $BOOT_HOST"
elif [ -w /dev/kvm ]; then ACCEL=KVM; else
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
# A PID FILE STILL PRESENT WHEN A PATH RETURNS IS A GUEST NOBODY STOPPED. qemu_boot.py
# unlinks its own on the way out, so one left behind means the boot lost its guest. That
# is a failure of the run and is reported as one, by name, per path.
#
# It used to be handled only by the EXIT trap, which runs AFTER `exit $rc` has fixed the
# status -- so an orphaned guest was killed in silence and the sweep reported success.
# lib/boot_host.py's agent has always named its leftovers and failed the run; its
# docstring says this one "could only ever be a count". Now it is not.
#
# AND THE PID IS CHECKED BEFORE IT IS SIGNALLED. "Only pids THIS run wrote" used to mean
# "whatever number is in the file", killed at exit -- up to minutes after the boot that
# wrote it, and a pid is exactly what a busy machine recycles. The promise not to touch
# somebody else's virtual machines was therefore never kept. /proc/<pid>/cmdline must
# name both qemu and this run's output directory before anything is signalled; anything
# else is reported and LEFT ALONE, which is the safe direction to be wrong in.
#
# MEASURED, so the comment does not outrun the code: the trap is also said to protect a
# run driven over ssh from a dropped connection. On dash here, both SIGINT and SIGHUP to
# the process group do run this trap and the guest is reaped -- checked 2026-09-20 with a
# setsid'd guest, which is what one that outlived its launcher looks like. The signal list
# is therefore left as it is rather than extended on a story.
PIDDIR="$OUT/pids"   # written by lib/build.sh, one per boot
mkdir -p "$PIDDIR"

# Is this pid one of ours? Linux-only, like the rest of this file's leak checks, and the
# same question lib/boot_host.py's _kill_orphans asks of the same file.
#
# HOW STRONG THIS IS, stated rather than implied. It is a SECOND guard: only pids written
# into $OUT/pids are ever looked at, and this asks whether the process still there is
# plausibly the one that wrote it. A recycled pid gets through only if the new process
# also names qemu and $OUT, so the guard is as specific as $OUT is -- `--out /tmp` on a
# machine running other people's virtual machines is a weak test, and `out/tier-c` is a
# strong one. Wrong in this direction means declining to kill, which is the safe way.
_is_ours() {
    [ -r "/proc/$1/cmdline" ] || return 1
    _cmd=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null) || return 1
    case "$_cmd" in
        *qemu*) ;;
        *) return 1 ;;
    esac
    case "$_cmd" in
        *"$OUT"*) return 0 ;;
        *) return 1 ;;
    esac
}

# Answer for every pid file left behind. Non-zero when anything was.
sweep_pids() {
    _leaked=0
    for pf in "$PIDDIR"/*.pid; do
        [ -e "$pf" ] || continue
        _leaked=1
        _pid=$(cat "$pf" 2>/dev/null)
        _name=$(basename "$pf")
        rm -f "$pf"
        if [ -z "$_pid" ] || ! kill -0 "$_pid" 2>/dev/null; then
            say "${R}LEAK${O} $_name was left behind; the guest it named is already gone"
        elif _is_ours "$_pid"; then
            kill -9 "$_pid" 2>/dev/null
            say "${R}LEAK${O} a guest was left running ($_name, pid $_pid); it has been killed"
        else
            say "${R}LEAK${O} $_name names pid $_pid, which is not this run's; left alone"
        fi
    done
    [ "$_leaked" = 0 ]
}
cleanup() {
    sweep_pids || :
    [ "$KEEP" = 1 ] || rm -rf "$PIDDIR"
}
trap 'cleanup' EXIT INT TERM

# ------------------------------------------------------- what is here already ----
# WHERE THE HARNESS PUTS THEM, which is not necessarily /tmp. qemu_boot.py makes its qmp
# socket directory with tempfile.mkdtemp(prefix="qb-"), and tempfile honours $TMPDIR, then
# $TEMP and $TMP. This counted /tmp/qb-* regardless, so with any of those pointing elsewhere
# it compared two counts of nothing and printed "clean" over a real leak --
# tests/unit/test_tier_c_run.py reproduces exactly that. Asking Python is the one way to
# name the directory Python will choose.
QB_DIR=$(python3 -c 'import tempfile; print(tempfile.gettempdir())') \
    || die "python3 could not name its temporary directory, so leaks cannot be counted"
QB_BEFORE=$(ls -d "$QB_DIR"/qb-* 2>/dev/null | wc -l | tr -d ' ')
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
    t_rc=0
    ./kitchen test "$ISO" "$flag" --out "$OUT" --seconds "$SECONDS_CEIL" \
        --golden "$GOLDEN" --record "$JSONL" || t_rc=$?
    # EACH PATH ANSWERS FOR ITS OWN LEFTOVERS, before the next one starts. One directory
    # is shared by every path and nothing emptied it, so a file left by the first was
    # still sitting there when the last finished -- and whatever it named was killed at
    # exit, under the wrong path's name, if it was noticed at all.
    sweep_pids || { say "${R}$path left a guest behind.${O}"; rc=1; }
    # STOP BEFORE THE LEDGER. 2 and 3 mean the boot host could not be used -- a broken
    # configuration, or a machine that could not be reached -- so no boot happened and
    # there is nothing to record. Carrying on would run the remaining paths against the
    # same unusable machine and then write a ledger whose rows are a claim about boots
    # that never took place, which is the one thing this file exists not to do.
    if [ "$t_rc" -ge 2 ]; then
        say ""
        say "${R}$path could not be run on the boot host (exit $t_rc).${O}"
        say "${R}No ledger was written: a row is a claim about a boot that happened.${O}"
        exit "$t_rc"
    fi
    [ "$t_rc" = 0 ] || rc=1
done

# ------------------------------------------------------------------- ledger ----
python3 - "$JSONL" "$LEDGER" "$TARGET" "$PROFILE" <<'PY' || rc=1
import json, os, subprocess, sys, datetime
jsonl, ledger, target, profile = sys.argv[1:5]

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

# THE MACHINE THAT BOOTED, read from what it wrote. The accelerator and the qemu version
# used to be measured HERE -- `-w /dev/kvm` above, and `qemu-system-x86_64 --version` on
# this machine's PATH -- which describe the machine running this script, not necessarily
# the one that booted. Every row carries both, from qemu_boot.py, the process that ran
# qemu. accel is kvm only if every row of this run is: one TCG boot makes it a TCG run.
accels = {r.get("accel") for r in rows}
qemus = {r.get("qemu") for r in rows}
if len(qemus) != 1 or not next(iter(qemus)) or next(iter(qemus)) == "unknown":
    print("  refusing to write the ledger: the rows do not name one qemu version\n"
          f"  ({', '.join(sorted(str(q) for q in qemus))}). A row without one came from a\n"
          "  qemu_boot.py older than this script, or from a qemu that would not say.",
          file=sys.stderr)
    raise SystemExit(1)
qemu = qemus.pop()
accel = "kvm" if accels == {"kvm"} else "tcg"
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
    "accel": accel,
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
QB_AFTER=$(ls -d "$QB_DIR"/qb-* 2>/dev/null | wc -l | tr -d ' ')
say ""
if [ -n "$BOOT_HOST" ]; then
    # COUNTING THIS MACHINE'S /tmp WOULD BE A CHECK THAT CANNOT FAIL. No qemu ran here, so
    # the count is 0 before and 0 after however badly the boots behaved, and the line would
    # print "clean" over anything. The real check moved WITH the boots and got stricter on
    # the way: each run on the boot host has a TMPDIR of its own, so a leak there is named
    # rather than counted, and it fails that run instead of this summary.
    say "${G}clean${O} each boot checked its own temporary directory on $BOOT_HOST"
elif [ "$QB_AFTER" -gt "$QB_BEFORE" ]; then
    say "${R}LEAK${O} $QB_DIR/qb-* went from $QB_BEFORE to $QB_AFTER: the harness left"
    say "     qmp socket directories behind."
    rc=1
else
    say "${G}clean${O} no qmp socket directories leaked in $QB_DIR (still $QB_AFTER)"
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
