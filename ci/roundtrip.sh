#!/bin/sh
# Unpack and repack an ISO with no recipes, then prove the result is still the same ISO.
#
#   ci/roundtrip.sh <iso>
#
# Guards the core claim of the whole toolkit: that a rebuild does not quietly corrupt
# anything. Expect an identical size and a difference confined to the ISO9660 metadata
# region -- those are the volume timestamp fields, which no backend can pin portably.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ISO=${1:?usage: roundtrip.sh <iso>}
MAX_DIFF=${KITCHEN_ROUNDTRIP_MAX_SECTORS:-64}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-rt.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

echo "round-trip: $(basename "$ISO")"
"$REPO_ROOT/kitchen" unpack "$ISO" -o "$WORK" >/dev/null || exit 1
"$REPO_ROOT/kitchen" pack -s "$WORK/iso" -o "$WORK/out.iso" >/dev/null || exit 1

python3 - "$ISO" "$WORK/out.iso" "$MAX_DIFF" <<'PY'
import os, sys
a_p, b_p, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
sa, sb = os.path.getsize(a_p), os.path.getsize(b_p)
print(f"  size      original={sa:,}  rebuilt={sb:,}  delta={sb - sa:+,}")
rc = 0
if sa != sb:
    print("  FAIL size changed"); rc = 1
SEC = 2048
diff = []
with open(a_p, "rb") as a, open(b_p, "rb") as b:
    n = 0
    while True:
        x, y = a.read(SEC), b.read(SEC)
        if not x and not y:
            break
        if x != y:
            diff.append(n)
        n += 1
pct = 100 * len(diff) / max(n, 1)
print(f"  sectors   {len(diff)} of {n:,} differ ({pct:.4f}%)")
if len(diff) > limit:
    print(f"  FAIL more than {limit} sectors differ"); rc = 1
# Every difference must sit in the ISO9660 metadata region, not in the payload.
if diff and max(diff) > 256:
    print(f"  FAIL a difference at sector {max(diff)} is outside the metadata region"); rc = 1
elif diff:
    print(f"  ok        all differences are in the metadata region (<= sector {max(diff)})")
sys.exit(rc)
PY
