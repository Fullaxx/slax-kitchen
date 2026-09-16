#!/bin/sh
# Boot a Slax ISO interactively under QEMU with legacy BIOS firmware.
#
#   tools/qemu/boot-bios.sh ISO [--display MODE] [--mem MiB]
#                               [--disk FILE [--disk-size N] [--boot-disk]]
#
#   --display vnc|curses|gtk|sdl|none   default vnc; see common.sh for why
#   --vnc-addr ADDR                     default 127.0.0.1 -- tunnel in, do not expose
#   --disk FILE                         attach a qcow2, creating it if absent
#   --boot-disk                         boot the disk instead of the ISO
#
# This is the one that works on a stock Slax ISO. Every Slax image boots on BIOS via
# isolinux; UEFI needs the uefi-bootable recipe first, which is why boot-uefi.sh checks.
set -u

# shellcheck source=tools/qemu/common.sh
. "$(dirname "$0")/common.sh"

FIRMWARE="SeaBIOS (legacy)"

qemu_parse_args "$@"
qemu_check_display
qemu_launch
