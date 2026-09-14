#!/bin/sh
# Busybox replacement gates 1-3 -- millisecond checks, no VM.
#
#   tests/busybox/gates.sh <candidate-busybox> <stock-initramfs-dir>
#
# The shipped i386 busybox EXECUTES DIRECTLY on an x86_64 host, so the stock binary is
# available as a reference oracle rather than only as a description. That is what makes
# these unit tests instead of boot tests: gates 4-5 (a real boot, and the rollback proof)
# stay in the host queue, but they become confirmation rather than the primary signal.
#
#   1  applet parity      candidate --list must cover every applet the boot scripts call,
#                         and the blkid/eject shadowing invariant must still hold
#   2  differential       run the VERBATIM pipelines livekitlib parses, under both
#                         busyboxes, over the same fixtures, and diff
#   3  script replay      source the REAL livekitlib under the candidate's ash and
#                         exercise its pure functions -- catches shell drift, not just
#                         applet output
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
CAND=${1:-}
STOCK_DIR=${2:-}
pass=0; fail=0; skip=0

R='\033[31m'; G='\033[32m'; Y='\033[33m'; O='\033[0m'
[ -t 1 ] || { R=''; G=''; Y=''; O=''; }

ok()   { pass=$((pass+1)); printf "  ${G}ok${O}   %s\n" "$1"; }
bad()  { fail=$((fail+1)); printf "  ${R}FAIL${O} %s\n" "$1"; }
note() { skip=$((skip+1)); printf "  ${Y}skip${O} %s\n" "$1"; }

[ -n "$CAND" ] || { echo "usage: $0 <candidate-busybox> <stock-initramfs-dir>" >&2; exit 2; }
[ -x "$CAND" ] || { echo "not executable: $CAND" >&2; exit 2; }
# Absolute, because one gate-2 case cd's into the fixture tree and a relative path would
# stop resolving there -- which showed up as a fake "du -s -h differs".
CAND=$(cd "$(dirname "$CAND")" && pwd)/$(basename "$CAND")
[ -n "$STOCK_DIR" ] && [ -d "$STOCK_DIR" ] && STOCK_DIR=$(cd "$STOCK_DIR" && pwd)

# busybox dispatches on argv[0]: invoked as `bb-broken` it looks for an applet of that
# name, fails, and answers `--list` with one line. gates.sh read that as "cannot run the
# i386 binary" and blamed the host's IA32 support -- a confident and wrong diagnosis.
# Run every candidate through a symlink named `busybox`, whatever the file is called.
LINKDIR=$(mktemp -d)
ln -sf "$CAND" "$LINKDIR/busybox"
CAND="$LINKDIR/busybox"
trap 'rm -rf "$LINKDIR"' EXIT

if ! "$CAND" --list >/dev/null 2>&1; then
    echo "cannot run $CAND on this host -- i386 binary, no IA32 support?" >&2
    echo "  the target kernel needs CONFIG_IA32_EMULATION for the same reason" >&2
    exit 2
fi

echo "busybox gates"
printf '  candidate: %s\n' "$("$CAND" 2>&1 | head -1)"

# ---------------------------------------------------------------- gate 1: parity ------
CLIST=$(mktemp); "$CAND" --list | sort > "$CLIST"

missing=""
while read -r a; do
    case "$a" in ''|\#*) continue ;; esac
    grep -Fxq "$a" "$CLIST" || missing="$missing $a"
done < "$HERE/required-applets.txt"

if [ -n "$missing" ]; then
    bad "gate 1: candidate is missing applets the boot scripts call:$missing"
else
    n=$(grep -vc '^#' "$HERE/required-applets.txt")
    ok "gate 1: all $n required applets present ($(wc -l < "$CLIST") total)"
fi

# The shadowing invariant. blkid and eject are REAL FILES in bin/ that take precedence
# over busybox's applets of the same name, and livekitlib parses `blkid -o full`, an
# option busybox's applet does not have. Both busyboxes list them; the invariant is that
# the initramfs keeps real binaries there.
if [ -n "$STOCK_DIR" ] && [ -d "$STOCK_DIR/bin" ]; then
    shadow_ok=1
    for s in blkid eject; do
        if [ -f "$STOCK_DIR/bin/$s" ] && [ ! -L "$STOCK_DIR/bin/$s" ]; then :; else
            bad "gate 1: bin/$s is not a real file in the reference tree"
            shadow_ok=0
        fi
        grep -Fxq "$s" "$CLIST" || note "gate 1: candidate has no $s applet to shadow (harmless)"
    done
    [ "$shadow_ok" = 1 ] && ok "gate 1: blkid and eject are real files, shadowing the applets"
fi

# Everything below needs the stock binary as an oracle.
STOCK=""
[ -n "$STOCK_DIR" ] && [ -x "$STOCK_DIR/bin/busybox" ] && STOCK="$STOCK_DIR/bin/busybox"
if [ -z "$STOCK" ]; then
    note "gates 2-3: no stock initramfs given, nothing to diff against"
    rm -f "$CLIST"
    printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
    [ "$fail" -eq 0 ]
    exit $?
fi
printf '  reference: %s\n' "$("$STOCK" 2>&1 | head -1)"

# ------------------------------------------------------- gate 2: differential output ---
# The fixture tree is committed so a busybox bump shows up as a reviewable diff rather
# than a silent behaviour change.
FIX=$(mktemp -d)
mkdir -p "$FIX/deep/nested/dir"
printf 'one\ntwo\nthree\n' > "$FIX/a.txt"
head -c 4096 /dev/zero > "$FIX/deep/big.bin"
touch -d '2019-03-07 11:22:33' "$FIX/a.txt"

# Each case is a shell snippet run with $BB bound to one busybox or the other. These are
# the VERBATIM pipelines livekitlib and shutdown parse -- not simplified versions.
run_case() {
    _name=$1; _snippet=$2
    _a=$(BB="$STOCK" FIX="$FIX" sh -c "$_snippet" 2>&1)
    _b=$(BB="$CAND"  FIX="$FIX" sh -c "$_snippet" 2>&1)
    if [ "$_a" = "$_b" ]; then
        ok "gate 2: $_name"
    else
        bad "gate 2: $_name differs"
        printf "        1.26.2: %s\n        cand  : %s\n" "$_a" "$_b"
    fi
}

# livekitlib:521 -- the only awk use in Slax, in the PXE path
run_case "awk 'NR % N'" 'printf "1\n2\n3\n4\n5\n" | $BB awk "NR % 2 == 1"'
# livekitlib sortmod -- sorts bundles by numeric prefix even in subdirectories
run_case "sortmod pipeline" \
  'printf "/m/03-x.sb\n/m/01-a.sb\n/m/10-z.sb\n/m/02-b.sb\n" | $BB sed -r "s,(.*/(.*)),\2:\1," | $BB sort -n | $BB cut -d : -f 2-'
# livekitlib:640 cmdline_value -- egrep/tr/cut over a cmdline-shaped string
run_case "cmdline_value pipeline" \
  'echo "vga=791 from=/dev/sdb1/slax perchdir=new toram" | $BB egrep -o "(^|[[:space:]])from=[^[:space:]]+" | $BB tr -d " " | $BB cut -d "=" -f 2- | $BB tail -n 1'
# filter_noload -- egrep -v with a |-joined list
run_case "filter_noload egrep -v" \
  'printf "01-core.sb\n05-chromium.sb\n06-devel.sb\n" | $BB egrep -v "05-chromium|06-devel"'
# date -r on a fixture with a pinned mtime
run_case "date -r" '$BB date -r "$FIX/a.txt" "+%Y-%m-%d %H:%M"'
# date --date, which needs CONFIG_LONG_OPTS. NOT "... UTC" -- busybox rejects that
# suffix, so the first version of this case compared two identical failures and passed.
# The round trip below is what date_diff_since_now actually performs.
run_case "date --date +%s" '$BB date -u --date "2019-03-07 11:22:33" "+%s"'
run_case "date -r | date --date round trip" \
  '$BB date -u --date "$($BB date -u -r "$FIX/a.txt")" "+%s"'
# du -s -h, parsed by the perch session picker
run_case "du -s -h" 'cd "$FIX" && $BB du -s -h . | $BB cut -f 1'
# seq, used to build the find_data retry loop
run_case "seq 1 5" '$BB seq 1 5 | $BB tr "\n" " "'
# df's first column -- livekitlib cuts field 1 off the last line
run_case "df field 1" '$BB df "$FIX" | $BB tail -n 1 | $BB cut -d " " -f 1'

# ---------------------------------------------------------- gate 3: script replay -------
# Source the REAL livekitlib under each busybox's ash, with PATH restricted to that
# busybox's applets, and compare. This is the only gate that can catch drift in the SHELL
# rather than in an applet -- filter_noload uses ${VAR//,/|}, which is a bashism that ash
# is not obliged to support and whose behaviour has moved before.
LIB="$STOCK_DIR/lib/livekitlib"
if [ ! -f "$LIB" ]; then
    note "gate 3: no livekitlib in the reference tree"
else
    replay() {
        _bb=$1
        _d=$(mktemp -d)
        "$_bb" --install -s "$_d" >/dev/null 2>&1 || {
            # --install needs a writable dir and symlink support; fall back to the few
            # applets the functions below actually reach.
            for a in cat sed sort cut egrep grep tr tail head; do
                ln -sf "$(readlink -f "$_bb")" "$_d/$a" 2>/dev/null
            done
        }
        PATH="$_d" "$_bb" ash -c '
            # cmdline_value reads /proc/cmdline, which we cannot fake, so stub it and
            # keep the filter logic under test -- that is the part with the bashism.
            . '"$LIB"' 2>/dev/null
            cmdline_value() { [ "$1" = noload ] && echo "05-chromium,06-devel"; }
            printf "/m/03-x.sb\n/m/01-a.sb\n/m/10-z.sb\n" | sortmod
            printf "01-core.sb\n05-chromium.sb\n06-devel.sb\n" | filter_noload
        ' 2>&1
        rm -rf "$_d"
    }
    sa=$(replay "$STOCK"); sb=$(replay "$CAND")
    if [ "$sa" = "$sb" ]; then
        ok "gate 3: sortmod + filter_noload replay identically under both ash builds"
    else
        bad "gate 3: livekitlib behaves differently under the candidate's ash"
        printf "        1.26.2:\n%s\n        cand:\n%s\n" "$sa" "$sb"
    fi
fi

rm -rf "$CLIST" "$FIX"
printf '\n%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
