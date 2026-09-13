#!/bin/sh
# Regenerate the committed inventories under docs/30-inventory/manifests/.
#
#   ci/gen-manifests.sh <iso> [<iso> ...]
#
# The manifests are generated artifacts kept in git on purpose: they are what the
# structure tests and `kitchen probe` compare against, and regenerating them needs the
# ISOs, which are deliberately not in the repo. Running this by hand and committing the
# diff is how a new upstream release gets adopted.
#
# Everything here is read-only and unprivileged. Bundles are read in place with
# `unsquashfs -o <offset>` rather than extracted, so a full run costs seconds and no
# disk. The target name comes from the ISO filename, which upstream's own releases and
# `kitchen fetch` both follow.
set -u
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$REPO_ROOT/docs/30-inventory/manifests
[ $# -gt 0 ] || { echo "usage: ci/gen-manifests.sh <iso> [<iso> ...]" >&2; exit 2; }

for t in xorriso unsquashfs xz cpio sha256sum python3; do
    command -v "$t" >/dev/null 2>&1 || { echo "missing tool: $t" >&2; exit 1; }
done

WORK=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-mf.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

for ISO in "$@"; do
    [ -f "$ISO" ] || { echo "no such ISO: $ISO" >&2; exit 1; }
    base=$(basename "$ISO" .iso)                        # slax-64bit-debian-12.2.0
    target=$(echo "$base" | sed -n 's/^slax-\([0-9]*bit\)-\([a-z]*\)-\(.*\)$/\2-\1-\3/p')
    [ -n "$target" ] || { echo "cannot parse target from $base" >&2; exit 1; }
    flavour=${target%%-*}
    echo "== $target"

    # --- /slax/boot/ ------------------------------------------------------------
    rm -rf "${WORK:?}/boot"
    xorriso -osirrox on -indev "$ISO" -extract /slax/boot "$WORK/boot" -- 2>/dev/null
    ( cd "$WORK/boot" && find . -type f | LC_ALL=C sort | xargs sha256sum ) \
        > "$OUT/bootfiles-$target.sha256"

    # --- initrfs.img ------------------------------------------------------------
    rm -rf "${WORK:?}/irfs" && mkdir -p "$WORK/irfs"
    ( cd "$WORK/irfs" && xz -dc "$WORK/boot/initrfs.img" | cpio -id --quiet 2>/dev/null )
    ( cd "$WORK/irfs" && find . -type f | LC_ALL=C sort | xargs sha256sum ) \
        > "$OUT/initramfs-$target.sha256"

    # --- packages ---------------------------------------------------------------
    # Bundle extents are read straight out of the ISO; nothing is extracted.
    xorriso -indev "$ISO" -find /slax/modules -exec report_lba -- 2>/dev/null \
      | awk -F, '/^File data/{gsub(/ /,"",$2); n=$5; gsub(/^ *.|.$/,"",n);
                 sub(/.*\//,"",n); print n, $2*2048}' | LC_ALL=C sort > "$WORK/bundles"

    if [ "$flavour" = debian ]; then
        # dpkg status lives only in 01-core; one row per installed package.
        off=$(awk '$1=="01-core.sb"{print $2}' "$WORK/bundles")
        rm -rf "${WORK:?}/pkg"
        unsquashfs -o "$off" -d "$WORK/pkg" -q "$ISO" var/lib/dpkg/status >/dev/null 2>&1
        python3 - "$WORK/pkg/var/lib/dpkg/status" > "$OUT/$target.packages.tsv" <<'PY'
import sys
cur = {}
rows = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.rstrip("\n")
    if not line:
        if cur.get("Package"):
            rows.append((cur["Package"], cur.get("Version", ""),
                         cur.get("Architecture", ""), cur.get("Status", "")))
        cur = {}
        continue
    if line[0] in " \t":
        continue
    k, _, v = line.partition(":")
    cur[k] = v.strip()
if cur.get("Package"):
    rows.append((cur["Package"], cur.get("Version", ""),
                 cur.get("Architecture", ""), cur.get("Status", "")))
for r in sorted(rows):
    print("\t".join(r))
PY
    else
        # Slackware records one file per package, per bundle, so attribute each row.
        : > "$OUT/$target.packages.tsv"
        while read -r name off; do
            rm -rf "${WORK:?}/pkg"
            unsquashfs -o "$off" -d "$WORK/pkg" -q "$ISO" \
                var/lib/pkgtools/packages >/dev/null 2>&1 || continue
            [ -d "$WORK/pkg/var/lib/pkgtools/packages" ] || continue
            ( cd "$WORK/pkg/var/lib/pkgtools/packages" && ls -1 ) \
              | LC_ALL=C sort | sed "s|^|${name%.sb}\t|" >> "$OUT/$target.packages.tsv"
        done < "$WORK/bundles"
    fi

    printf '   boot=%-4s initramfs=%-4s packages=%s\n' \
        "$(wc -l < "$OUT/bootfiles-$target.sha256")" \
        "$(wc -l < "$OUT/initramfs-$target.sha256")" \
        "$(wc -l < "$OUT/$target.packages.tsv")"
done
