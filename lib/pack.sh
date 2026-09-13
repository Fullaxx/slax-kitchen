#!/bin/sh
# kitchen pack -- rebuild an ISO from a work tree.
#
# TWO BACKENDS, deliberately. Measured against the stock Slax ISO:
#
#   genisoimage  faithful. Reproduces upstream's own build byte-for-byte except for
#                three PVD timestamp fields -- 19 differing sectors out of 212,819
#                (0.009%), identical total size. This is what Tomas M. actually uses
#                (see vendor/linux-live/build and /usr/bin/genslaxiso). It cannot pin
#                dates (cdrkit dropped cdrtools' -*-date flags) and cannot emit a
#                hybrid GPT on its own.
#
#   xorriso      required for UEFI (-eltorito-alt-boot) and isohybrid MBR/GPT. Accepts
#                --modification-date so builds are reproducible. Caveat: it enforces
#                ECMA-119 d-characters and UPPERCASES the application id, so a stock
#                'slax' becomes 'SLAX'. Cosmetic -- nothing in livekitlib reads it --
#                but it does mean an xorriso rebuild will not fingerprint-match a stock
#                ISO on that one field.
#
# Default: genisoimage when neither --uefi nor --hybrid is requested, xorriso otherwise.

kitchen_pack() {
    src="work/iso" out="" backend="" uefi=0 hybrid=0 volid="slax" appid="slax" sysid="LINUX" mdate=""
    while [ $# -gt 0 ]; do
        case "$1" in
            -o|--output)  out=$2; shift 2 ;;
            -s|--source)  src=$2; shift 2 ;;
            --backend)    backend=$2; shift 2 ;;
            --uefi)       uefi=1; shift ;;
            --hybrid)     hybrid=1; shift ;;
            --volid)      volid=$2; shift 2 ;;
            --appid)      appid=$2; shift 2 ;;
            --date)       mdate=$2; shift 2 ;;
            -*) die "pack: unknown option $1" ;;
            *)  out=$1; shift ;;
        esac
    done
    [ -n "$out" ] || die "pack: need -o <output.iso>"
    [ -d "$src" ] || die "pack: no work tree at $src (run 'kitchen unpack' first)"
    [ -f "$src/slax/boot/isolinux.bin" ] || die "pack: $src does not look like a Slax tree (no slax/boot/isolinux.bin)"

    if [ -z "$backend" ]; then
        if [ "$uefi" = 1 ] || [ "$hybrid" = 1 ]; then backend=xorriso; else backend=genisoimage; fi
    fi
    if [ "$backend" = genisoimage ] && { [ "$uefi" = 1 ] || [ "$hybrid" = 1 ]; }; then
        die "pack: --uefi/--hybrid need the xorriso backend (genisoimage cannot emit a hybrid GPT)"
    fi
    have "$backend" || die "pack: $backend not installed"

    mkdir -p "$(dirname "$out")"
    printf '%spack%s %s -> %s  (backend: %s)\n' "$B" "$O" "$src" "$out" "$backend"

    case "$backend" in
      genisoimage)
        genisoimage -o "$out" -quiet -J -R -D \
            -A "$appid" -V "$volid" -sysid "$sysid" -input-charset utf-8 \
            -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
            -no-emul-boot -boot-info-table -boot-load-size 4 \
            "$src" || die "pack: genisoimage failed"
        ;;
      xorriso)
        set -- -as mkisofs -o "$out" -J -R -D \
               -A "$appid" -V "$volid" -sysid "$sysid" -input-charset utf-8 \
               -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
               -no-emul-boot -boot-load-size 4 -boot-info-table
        [ -n "$mdate" ] && set -- "$@" --modification-date="$mdate"
        if [ "$hybrid" = 1 ]; then
            hdr=/usr/lib/ISOLINUX/isohdpfx.bin
            [ -f "$hdr" ] || die "pack: $hdr missing (apt-get install isolinux)"
            set -- "$@" -isohybrid-mbr "$hdr" -partition_offset 16
        fi
        if [ "$uefi" = 1 ]; then
            [ -f "$src/boot/efi.img" ] || \
                die "pack: --uefi needs $src/boot/efi.img (apply the 'uefi-bootable' recipe first)"
            set -- "$@" -eltorito-alt-boot -e boot/efi.img -no-emul-boot
            [ "$hybrid" = 1 ] && set -- "$@" -isohybrid-gpt-basdat
        fi
        xorriso "$@" "$src" 2>&1 | grep -iE 'failure|sorry' && die "pack: xorriso failed"
        ;;
      *) die "pack: unknown backend '$backend' (want genisoimage or xorriso)" ;;
    esac

    printf '  %sok%s   %s  (%s bytes)\n' "$G" "$O" "$out" "$(stat -c%s "$out")"
    [ "$backend" = xorriso ] && [ "$appid" != "$(printf '%s' "$appid" | tr a-z A-Z)" ] && \
        printf '  %snote: xorriso uppercased the application id to %s%s\n' "$Y" \
               "$(printf '%s' "$appid" | tr a-z A-Z)" "$O"
    return 0
}
