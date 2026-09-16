#!/bin/sh
# Shared helpers for the interactive QEMU boot scripts. Sourced, never run.
#
#   . "$(dirname "$0")/common.sh"
#
# WHY THIS IS NOT UNDER tests/. Everything in tests/ is the automated tier: headless,
# assertion-driven, run by CI. tests/boot/qemu_boot.py is -display none, -serial to a
# file, one QMP screendump, assert, exit. That proves an image did not regress. It cannot
# tell you the desktop came up, the browser launches, or the thing is usable -- those need
# a human and a window, which is what these scripts are for.
#
# WHY VNC IS THE DEFAULT, and it is not a preference. Measured on both the dev container
# and the build host on 2026-09-16: `qemu-system-x86_64 -display help` lists exactly
# `none` and `curses`, and `ldd` finds zero GUI libraries. Debian's qemu-system-x86 is
# built without GTK and SDL. VNC is compiled in separately and works, so on these machines
# it is the ONLY way to see a framebuffer at all. It also tunnels over ssh, which is what
# you want when the ISO lives on a server.
#
# HOW TO PROBE QEMU FOR A FEATURE, because the obvious way is wrong. `-vnc help` prints
# the option list and then exits NON-ZERO, so
#     qemu-system-x86_64 -vnc help >/dev/null 2>&1 && echo supported
# reports a false negative -- it briefly had this script's author recording that the build
# host had no VNC when it has. Count what the help prints; never test its exit status.
set -u

PROG=$(basename "$0")

MEM=2048
DISPLAY_MODE=vnc          # not DISPLAY -- that is the X11 variable and must not be touched
VNC_ADDR=127.0.0.1
DISK=""
DISK_SIZE=8G
BOOT_DISK=0
FORCE=0
ISO=""

have() { command -v "$1" >/dev/null 2>&1; }
die()  { printf '%s: %s\n' "$PROG" "$*" >&2; exit 2; }
note() { printf '  %s\n' "$*" >&2; }

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# Long+short pairs, `shift 2` for value options, exit 2 for usage errors -- the shape
# tools/build-busybox.sh uses.
qemu_parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --display)    DISPLAY_MODE=$2; shift 2 ;;
            --vnc-addr)   VNC_ADDR=$2;     shift 2 ;;
            --mem|-m)     MEM=$2;          shift 2 ;;
            --disk)       DISK=$2;         shift 2 ;;
            --disk-size)  DISK_SIZE=$2;    shift 2 ;;
            --boot-disk)  BOOT_DISK=1;     shift ;;
            --force)      FORCE=1;         shift ;;
            -h|--help)    usage ;;
            -*)           die "unknown option: $1" ;;
            *)            [ -z "$ISO" ] || die "more than one ISO given: $ISO and $1"
                          ISO=$1; shift ;;
        esac
    done

    [ -n "$ISO" ] || die "need an ISO. Try --help."
    [ -f "$ISO" ] || die "no such file: $ISO"
    have qemu-system-x86_64 || die "qemu-system-x86_64 not installed (apt-get install qemu-system-x86)"

    case "$MEM" in *[!0-9]*) die "--mem wants a number of MiB, got: $MEM" ;; esac
    [ "$BOOT_DISK" = 0 ] || [ -n "$DISK" ] || die "--boot-disk needs --disk"
}

# "Is this display backend built into this qemu?" -- see the probe warning above.
qemu_display_ok() {
    case "$1" in
        vnc)  [ "$(qemu-system-x86_64 -vnc help 2>&1 | grep -c '=<')" -gt 0 ] ;;
        *)    qemu-system-x86_64 -display help 2>&1 | tail -n +2 | grep -qx "[[:space:]]*$1" ;;
    esac
}

# Warn rather than refuse: this reads a qemu build's capabilities, and being wrong about
# them should cost a confusing qemu error, not a refusal to try.
qemu_check_display() {
    qemu_display_ok "$DISPLAY_MODE" && return 0
    note "warning: this qemu does not list '$DISPLAY_MODE' as a display backend."
    note "         it has: $(qemu-system-x86_64 -display help 2>&1 | tail -n +2 | tr -d ' ' | tr '\n' ' ')plus vnc"
    note "         trying anyway; use --display to pick another."
}

# Every download and firmware path in this repo is pinned or discovered deliberately.
# tests/boot/qemu_boot.py hardcodes these two absolute paths and dies with a traceback
# when OVMF_VARS is absent; search a short list instead and name the package on failure.
qemu_find_ovmf() {
    OVMF_CODE=${OVMF_CODE:-}
    OVMF_VARS=${OVMF_VARS:-}
    for d in /usr/share/OVMF /usr/share/ovmf /usr/share/edk2/ovmf /usr/share/qemu; do
        [ -n "$OVMF_CODE" ] || for f in "$d/OVMF_CODE_4M.fd" "$d/OVMF_CODE.fd" "$d/OVMF.fd"; do
            [ -f "$f" ] && { OVMF_CODE=$f; break; }
        done
        [ -n "$OVMF_VARS" ] || for f in "$d/OVMF_VARS_4M.fd" "$d/OVMF_VARS.fd"; do
            [ -f "$f" ] && { OVMF_VARS=$f; break; }
        done
    done
    [ -n "$OVMF_CODE" ] || die "no OVMF firmware found (apt-get install ovmf), or set \$OVMF_CODE"
    [ -f "$OVMF_CODE" ] || die "\$OVMF_CODE is not a file: $OVMF_CODE"
    [ -n "$OVMF_VARS" ] || die "found $OVMF_CODE but no matching OVMF_VARS (set \$OVMF_VARS)"
    [ -f "$OVMF_VARS" ] || die "\$OVMF_VARS is not a file: $OVMF_VARS"
}

# Refusing here is the whole point. Stock Slax has no EFI boot entry -- that is upstream
# issue 1 -- so a UEFI run against an unpatched ISO sits in an EFI shell until you give up.
# Saying so costs a second; finding out costs a TCG boot.
qemu_require_uefi_iso() {
    if ! have xorriso; then
        note "note: xorriso not installed, so the ISO was NOT checked for a UEFI boot entry."
        note "      if this lands in an EFI shell, that is why (apt-get install xorriso)."
        return 0
    fi
    if xorriso -indev "$ISO" -report_el_torito plain 2>/dev/null | grep -q 'UEFI'; then
        return 0
    fi
    [ "$FORCE" = 1 ] && { note "warning: no UEFI boot entry; --force given, continuing."; return 0; }
    cat >&2 <<EOF
$PROG: $(basename "$ISO") has no UEFI boot entry.

  El Torito lists BIOS only, so OVMF has nothing to load and you will land in an
  EFI shell. Stock Slax cannot boot on UEFI at all -- that is upstream issue 1.

  Fix it by applying the uefi-bootable recipe, or by building through a profile
  that already includes it:

      ./kitchen apply uefi-bootable -w work && ./kitchen pack -s work/iso
      ./kitchen build browsers

  Boot it anyway with --force, or use boot-bios.sh instead.
EOF
    exit 2
}

# Takes the firmware arguments, if any, as its own positional parameters; appends the
# common ones and execs. Building argv with `set --` is how POSIX sh does an array.
qemu_launch() {
    accel="TCG (no /dev/kvm -- 10-20x slower)"
    if [ -w /dev/kvm ]; then
        set -- "$@" -enable-kvm
        accel="KVM"
    fi

    set -- "$@" -m "$MEM" -vga std -boot d -cdrom "$ISO" -serial mon:stdio

    if [ -n "$DISK" ]; then
        if [ ! -f "$DISK" ]; then
            have qemu-img || die "qemu-img not installed (apt-get install qemu-utils)"
            qemu-img create -f qcow2 "$DISK" "$DISK_SIZE" >/dev/null \
                || die "could not create $DISK"
            note "created $DISK ($DISK_SIZE, qcow2)"
        fi
        # No if=, so it lands on the default bus as an IDE disk: /dev/sda in the guest,
        # which is what Slax's bootinst.sh expects to find and write an MBR to.
        set -- "$@" -drive "file=$DISK,format=qcow2"
        [ "$BOOT_DISK" = 1 ] && set -- "$@" -boot c
    fi

    case "$DISPLAY_MODE" in
        vnc) set -- "$@" -vnc "$VNC_ADDR:0,to=99" ;;
        *)   set -- "$@" -display "$DISPLAY_MODE" ;;
    esac

    printf '\n  iso       %s (%s MiB)\n' "$(basename "$ISO")" \
           "$(( $(wc -c < "$ISO") / 1048576 ))"
    printf '  firmware  %s\n  accel     %s\n  memory    %s MiB\n' "$FIRMWARE" "$accel" "$MEM"
    [ -n "$DISK" ] && printf '  disk      %s%s\n' "$DISK" \
        "$([ "$BOOT_DISK" = 1 ] && echo '  (booting from it)' || echo '')"
    if [ "$DISPLAY_MODE" = vnc ]; then
        printf '  display   vnc on %s:5900 (qemu prints the real port below if taken)\n' "$VNC_ADDR"
        printf '\n  from your workstation:\n    ssh -L 5900:127.0.0.1:5900 %s\n    vncviewer localhost:5900\n' \
               "$(hostname)"
    else
        printf '  display   %s\n' "$DISPLAY_MODE"
    fi
    printf '\n  this terminal is the qemu monitor. Ctrl-a c switches to it, `quit` exits.\n'
    printf '  the serial line stays silent: no Slax menu entry sets console=ttyS0.\n\n'

    exec qemu-system-x86_64 "$@"
}
