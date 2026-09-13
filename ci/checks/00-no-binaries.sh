#!/bin/sh
# stages: pre-commit pre-push ci
# desc: Reject ISOs, squashfs bundles, disk images and oversized files.
#
# THE most important gate in this repo. isos/ holds ~1.7 GB of base ISOs; a single
# `git add -A` would commit them permanently into history. .gitignore alone is not
# enough -- `git add -f` and explicit paths bypass it, and history cannot be un-fattened.
. "$(dirname "$0")/../lib.sh"

MAX_BYTES=${KITCHEN_MAX_FILE_BYTES:-2097152}   # 2 MiB

# Extensions that are never legitimate source in this repo.
is_forbidden_ext() {
    case "$1" in
        *.iso|*.sb|*.img|*.squashfs|*.cpio|*.tar|*.tar.*|*.tgz|*.txz|*.deb|*.rpm) return 0 ;;
        *.efi|*.c32|*.e64|*.ko|*.so|*.so.*)                                       return 0 ;;
        */vmlinuz|vmlinuz|*/mbr.bin|*/isolinux.bin)                                return 0 ;;
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
        [ "$sz" -gt "$MAX_BYTES" ] 2>/dev/null && echo "TOO_BIG $sz $f"
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
    esac
done < /tmp/.kitchen-binguard.$$
rm -f /tmp/.kitchen-binguard.$$

check_result
