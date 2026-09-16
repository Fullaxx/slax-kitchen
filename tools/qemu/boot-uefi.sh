#!/bin/sh
# Boot a Slax ISO interactively under QEMU with UEFI firmware (OVMF).
#
#   tools/qemu/boot-uefi.sh ISO [--display MODE] [--mem MiB] [--force]
#                               [--disk FILE [--disk-size N] [--boot-disk]]
#
#   --force        boot even when the ISO has no UEFI entry (you get an EFI shell)
#   $OVMF_CODE     override firmware discovery
#   $OVMF_VARS     override firmware discovery
#
# REFUSES AN ISO WITH NO EFI BOOT ENTRY, which is most of them. Stock Slax ships one
# El Torito image, platform BIOS -- upstream issue 1 -- so OVMF finds nothing to load and
# drops to a shell. Apply the uefi-bootable recipe first.
#
# OVMF_VARS IS COPIED PER RUN. The packaged one is read-only and shared; UEFI writes its
# variables, so pointing qemu at the original either fails or leaks state between runs.
set -u

# shellcheck source=tools/qemu/common.sh
. "$(dirname "$0")/common.sh"

FIRMWARE="OVMF (UEFI)"

qemu_parse_args "$@"
qemu_check_display
qemu_require_uefi_iso
qemu_find_ovmf

VARS=$(mktemp "${TMPDIR:-/tmp}/slax-ovmf-vars.XXXXXX") || die "could not make a temp file"
trap 'rm -f "$VARS"' EXIT INT TERM
cp "$OVMF_VARS" "$VARS" || die "could not copy $OVMF_VARS"

qemu_launch -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
            -drive "if=pflash,format=raw,file=$VARS"
