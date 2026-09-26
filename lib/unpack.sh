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
            -h|--help)      usage_cmd unpack; return 0 ;;
            -*) die "unpack: unknown option $1" ;;
            *)  iso=$1; shift ;;
        esac
    done
    [ -n "$iso" ] || die "unpack: need an ISO path"
    [ -f "$iso" ] || die "unpack: no such file: $iso"
    have xorriso || die "unpack: xorriso not installed (apt-get install xorriso)"

    # THE RECORD GOES WITH THE TREE. <dest>/.kitchen says what was applied to <dest>/iso,
    # what that fetched and built, and what pack is to master it with. --force replaced the
    # tree and kept the record, so a fresh tree was described as the old one: `status`
    # listed recipes it did not have, `apply` refused to apply them again and advised
    # unpacking the base -- which had just been done -- and `pack` put them in the sidecar
    # and mastered the old hints. A stale `uefi: true` demanded the recipe `apply` refused,
    # and a stale volid named the image after a recipe it never had. A record whose tree was
    # deleted by hand is the same record, so it is refused the same way.
    tree="$dest/iso"
    if [ -e "$tree" ]; then
        [ "$force" = 1 ] || die "unpack: $tree exists (use --force to replace)"
    elif [ -e "$dest/.kitchen" ]; then
        [ "$force" = 1 ] || die "unpack: $dest/.kitchen records a tree that is no longer" \
                                "there (use --force to start over)"
    fi
    if [ "$force" = 1 ]; then
        rm -rf "$tree" "$dest/.kitchen"
    fi
    mkdir -p "$dest"

    printf '%sunpack%s %s -> %s\n' "$B" "$O" "$iso" "$tree"
    # A FAILED EXTRACTION IS A STATUS, A WORD, OR NOTHING THERE. This piped xorriso into
    # grep for FAILURE or SORRY, which threw the exit status away -- /bin/sh has no
    # PIPESTATUS, as pack.sh found -- so an xorriso that died printing nothing (measured:
    # exit 127, no such line) was "ok 0 files", with an origin.yaml for a tree that was
    # never there. xorriso_run (in kitchen) judges the run now, by lib/diff.py's rule. And
    # a file that is not an ISO at all extracts nothing, with exit 0 and no such line
    # (xorriso 1.5.6), so the tree must also hold what pack looks for. On either failure
    # the tree goes: it did not exist before this run, because an existing one was
    # refused or removed above.
    if ! xorriso_run -osirrox on -indev "$iso" -extract / "$tree"; then
        rm -rf "$tree"
        die "unpack: xorriso failed (exit $XORRISO_RC)"
    fi
    if [ ! -f "$tree/slax/boot/isolinux.bin" ]; then
        rm -rf "$tree"
        die "unpack: $iso is not a Slax ISO (no slax/boot/isolinux.bin in it)"
    fi

    # WHAT THE IMAGE BOOTS WITH: its BIOS and UEFI entries and a hybrid MBR, which were
    # written when it was mastered and so are not in the tree. pack warns about any of them a
    # build will not write again (LAYERING.md, step 6), and they are read by lib/isoparse.py,
    # the reader the structure test uses. An image it cannot read records none of them, and
    # that is not a failure: the tree is already here, and pack reads a missing one as unknown.
    _boot=$(python3 "$REPO_ROOT/lib/isoparse.py" --boot "$iso") || _boot=""

    # Record provenance so pack/ diff/ probe can reason about where this came from.
    mkdir -p "$dest/.kitchen"
    {
        echo "source_iso: $(cd "$(dirname "$iso")" && pwd)/$(basename "$iso")"
        echo "source_sha256: $(sha256sum "$iso" | cut -d' ' -f1)"
        # -L: sha256sum follows a symlinked ISO, so the size must too. Without it a linked
        # download cache recorded the LINK's size -- 66 bytes beside the sha256 of 416 MiB --
        # and the provenance sidecar carried the same wrong number into out/.
        echo "source_size: $(stat -L -c%s "$iso")"
        echo "unpacked_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        if [ -n "$_boot" ]; then printf '%s\n' "$_boot"; fi
    } > "$dest/.kitchen/origin.yaml"

    n=$(find "$tree" -type f | wc -l)
    printf '  %sok%s   %s files, %s\n' "$G" "$O" "$n" "$(du -sh "$tree" | cut -f1)"
    printf '  %sprovenance recorded in %s/.kitchen/origin.yaml%s\n' "$D" "$dest" "$O"
}
