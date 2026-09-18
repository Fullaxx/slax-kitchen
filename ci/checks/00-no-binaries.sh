#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Reject ISOs, squashfs bundles, disk images and oversized files.
#
# THE most important gate in this repo. isos/ holds ~1.7 GB of base ISOs; a single
# `git add -A` would commit them permanently into history. .gitignore alone is not
# enough -- `git add -f` and explicit paths bypass it, and history cannot be un-fattened.
. "$(dirname "$0")/../lib.sh"

MAX_BYTES=${KITCHEN_MAX_FILE_BYTES:-2097152}   # 2 MiB

# SAY SO WHEN THE BAR IS LOWERED FROM OUTSIDE. The variable stays -- a fork with a genuinely
# large committed asset needs a way through -- but raising the limit is a decision, and a
# decision made in an environment variable that nothing prints is one nobody can review.
[ "$MAX_BYTES" = 2097152 ] || note "00-no-binaries: KITCHEN_MAX_FILE_BYTES raises the limit to $MAX_BYTES bytes"

# Extensions that are never legitimate source in this repo.
is_forbidden_ext() {
    case "$1" in
        *.iso|*.sb|*.img|*.squashfs|*.cpio|*.tar|*.tar.*|*.tgz|*.txz|*.deb|*.rpm) return 0 ;;
        *.efi|*.c32|*.e64|*.ko|*.so|*.so.*)                                       return 0 ;;
        */vmlinuz|vmlinuz|*/mbr.bin|*/isolinux.bin)                                return 0 ;;
        # Windows payloads, which had NO extension rule at all -- so one under 2 MiB walked
        # straight through, and KITCHEN_MAX_FILE_BYTES could raise that bar from outside the
        # repo. The documented pattern is to FETCH a payload at build time and never commit
        # it: bundle.fromTarball and bundle.files exist for that, and tor-browser.yaml pulls
        # 138 MB rather than vendoring a byte. This list is what stops the shortcut. Issue #19.
        *.exe|*.dll|*.msi|*.sys|*.cab)                                            return 0 ;;
    esac
    return 1
}

# Directories that are build scratch or download caches.
is_forbidden_dir() {
    case "$1" in
        isos/*|work/*|out/*) return 0 ;;
    esac
    return 1
}

check_files_nl | while IFS= read -r f; do
    if is_forbidden_dir "$f"; then
        echo "FORBIDDEN_DIR $f"
    elif is_forbidden_ext "$f"; then
        echo "FORBIDDEN_EXT $f"
    else
        sz=$(file_size "$f")
        # NO `2>/dev/null` HERE. It used to hide `[ MISSING -gt N ]` -- and before that,
        # a file_size that answered 0 for everything -- behind a silent false. A size that
        # is not a number is now a reported failure, not a quiet pass. Issue #19.
        case "$sz" in
            ''|*[!0-9]*) echo "UNMEASURED $f" ;;
            *) [ "$sz" -gt "$MAX_BYTES" ] && echo "TOO_BIG $sz $f" ;;
        esac
    fi
done > /tmp/.kitchen-binguard.$$ 2>/dev/null

while IFS= read -r line; do
    case "$line" in
        FORBIDDEN_DIR\ *)
            fail "build artifact / download cache: ${line#FORBIDDEN_DIR }" ;;
        FORBIDDEN_EXT\ *)
            fail "binary artifact must not be committed: ${line#FORBIDDEN_EXT }" ;;
        TOO_BIG\ *)
            rest=${line#TOO_BIG }; sz=${rest%% *}; path=${rest#* }
            fail "file is ${sz} bytes (limit ${MAX_BYTES}): ${path}" ;;
        UNMEASURED\ *)
            fail "size could not be measured, so the limit was not applied: ${line#UNMEASURED }" ;;
    esac
done < /tmp/.kitchen-binguard.$$
rm -f /tmp/.kitchen-binguard.$$

check_result
