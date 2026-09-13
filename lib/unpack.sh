#!/bin/sh
# kitchen unpack -- explode an ISO into an editable work tree.
#
# Uses xorriso's osirrox mode rather than 7z or a loop mount: it restores Rock Ridge
# names and permission bits faithfully (verified: bootinst.sh and extlinux.x* come back
# as 0755, everything else 0644), and it needs no privileges at all. A loop mount would
# need CAP_SYS_ADMIN, which the dev container does not have.

kitchen_unpack() {
    iso="" dest="work" force=0
    while [ $# -gt 0 ]; do
        case "$1" in
            -o|--output) dest=$2; shift 2 ;;
            -f|--force)  force=1; shift ;;
            -*) die "unpack: unknown option $1" ;;
            *)  iso=$1; shift ;;
        esac
    done
    [ -n "$iso" ] || die "unpack: need an ISO path"
    [ -f "$iso" ] || die "unpack: no such file: $iso"
    have xorriso || die "unpack: xorriso not installed (apt-get install xorriso)"

    tree="$dest/iso"
    if [ -e "$tree" ]; then
        [ "$force" = 1 ] || die "unpack: $tree exists (use --force to replace)"
        rm -rf "$tree"
    fi
    mkdir -p "$dest"

    printf '%sunpack%s %s -> %s\n' "$B" "$O" "$iso" "$tree"
    xorriso -osirrox on -indev "$iso" -extract / "$tree" 2>&1 \
        | grep -iE 'failure|sorry' && die "unpack: xorriso failed"

    # Record provenance so pack/ diff/ probe can reason about where this came from.
    mkdir -p "$dest/.kitchen"
    {
        echo "source_iso: $(cd "$(dirname "$iso")" && pwd)/$(basename "$iso")"
        echo "source_sha256: $(sha256sum "$iso" | cut -d' ' -f1)"
        echo "source_size: $(stat -c%s "$iso")"
        echo "unpacked_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$dest/.kitchen/origin.yaml"

    n=$(find "$tree" -type f | wc -l)
    printf '  %sok%s   %s files, %s\n' "$G" "$O" "$n" "$(du -sh "$tree" | cut -f1)"
    printf '  %sprovenance recorded in %s/.kitchen/origin.yaml%s\n' "$D" "$dest" "$O"
}
