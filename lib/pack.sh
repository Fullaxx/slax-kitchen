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
#
# OUTPUT PATH. With no -o, the ISO lands in ./out/ under a name derived from whatever
# the work tree was unpacked from (recorded in <work>/.kitchen/origin.yaml at unpack
# time), with a "-custom" suffix so it can never be confused with the stock download:
#
#     kitchen unpack isos/slax-64bit-debian-12.2.0.iso
#     kitchen pack                 ->  out/slax-64bit-debian-12.2.0-custom.iso
#
# -o accepts a file path OR a directory (in which case the derived name is used inside
# it). An existing file is never overwritten without --force.

# Work out the default output filename from the work tree's provenance.
_pack_derive_name() {
    _wt=$(dirname "$1")                       # work/iso -> work
    _origin="$_wt/.kitchen/origin.yaml"
    _base=""
    [ -f "$_origin" ] && _base=$(sed -n 's|^source_iso: .*/||p' "$_origin" | sed 's/\.iso$//')
    [ -n "$_base" ] || _base="slax"
    echo "${_base}-custom.iso"
}

kitchen_pack() {
    src="work/iso" out="" backend="" uefi=0 hybrid=0 volid="slax" appid="slax" sysid="LINUX" mdate="" force=0
    publisher="" preparer="" _sums="" volid_set=0 appid_set=0 sysid_set=0
    while [ $# -gt 0 ]; do
        case "$1" in
            -o|--output)  out=$2; shift 2 ;;
            -f|--force)   force=1; shift ;;
            -s|--source)  src=$2; shift 2 ;;
            --backend)    backend=$2; shift 2 ;;
            --uefi)       uefi=1; shift ;;
            --hybrid)     hybrid=1; shift ;;
            --volid)      volid=$2; volid_set=1; shift 2 ;;
            --appid)      appid=$2; appid_set=1; shift 2 ;;
            --date)       mdate=$2; shift 2 ;;
            -*) die "pack: unknown option $1" ;;
            *)  out=$1; shift ;;
        esac
    done
    [ -d "$src" ] || die "pack: no work tree at $src (run 'kitchen unpack' first)"

    # Default destination: ./out/<source-name>-custom.iso
    if [ -z "$out" ]; then
        out="out/$(_pack_derive_name "$src")"
    elif [ -d "$out" ]; then
        out="${out%/}/$(_pack_derive_name "$src")"
    fi
    case "$out" in *.iso) ;; *) out="$out.iso" ;; esac

    if [ -e "$out" ] && [ "$force" != 1 ]; then
        die "pack: $out already exists (use --force to overwrite)"
    fi
    [ -f "$src/slax/boot/isolinux.bin" ] || die "pack: $src does not look like a Slax tree (no slax/boot/isolinux.bin)"

    # Recipes record intent in <work>/.kitchen/pack.yaml rather than knowing xorriso
    # flags themselves: boot.isohybrid sets hybrid=true, boot.uefi sets uefi=true.
    # An explicit --uefi/--hybrid on the command line still wins.
    _hints="$(dirname "$src")/.kitchen/pack.yaml"
    if [ -f "$_hints" ]; then
        grep -q '^hybrid: *true' "$_hints" && [ "$hybrid" = 0 ] && {
            hybrid=1; printf '  %shint%s hybrid MBR/GPT requested by a recipe\n' "$D" "$O"; }
        grep -q '^uefi: *true' "$_hints" && [ "$uefi" = 0 ] && {
            uefi=1; printf '  %shint%s UEFI El Torito entry requested by a recipe\n' "$D" "$O"; }

        # iso.metadata records volume descriptor fields here, because there is nowhere
        # in the tree they could live -- they are set by the mastering tool. An explicit
        # CLI flag still wins, which is why each is only taken when unset.
        for _k in volid appid sysid publisher preparer; do
            _v=$(sed -n "s/^$_k: *//p" "$_hints" | head -1 | sed "s/^[\"']//;s/[\"']$//")
            [ -z "$_v" ] && continue
            _taken=1
            case "$_k" in
                volid)     if [ "$volid_set" = 0 ]; then volid=$_v; else _taken=0; fi ;;
                appid)     if [ "$appid_set" = 0 ]; then appid=$_v; else _taken=0; fi ;;
                sysid)     if [ "$sysid_set" = 0 ]; then sysid=$_v; else _taken=0; fi ;;
                publisher) publisher=$_v ;;
                preparer)  preparer=$_v ;;
            esac
            if [ "$_taken" = 1 ]; then
                printf '  %shint%s %s=%s\n' "$D" "$O" "$_k" "$_v"
            else
                printf '  %shint%s %s=%s overridden on the command line\n' "$D" "$O" "$_k" "$_v"
            fi
        done
        _sums=$(sed -n 's/^checksums: *//p' "$_hints" | head -1)
    fi

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
            ${publisher:+-publisher "$publisher"} ${preparer:+-p "$preparer"} \
            -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
            -no-emul-boot -boot-info-table -boot-load-size 4 \
            "$src" || die "pack: genisoimage failed"
        ;;
      xorriso)
        set -- -as mkisofs -o "$out" -J -R -D \
               -A "$appid" -V "$volid" -sysid "$sysid" -input-charset utf-8 \
               -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
               -no-emul-boot -boot-load-size 4 -boot-info-table
        [ -n "$publisher" ] && set -- "$@" -publisher "$publisher"
        [ -n "$preparer" ] && set -- "$@" -p "$preparer"
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
        # Check the exit status AND the log, not just the log. Grepping a pipeline
        # throws xorriso's status away (this is /bin/sh, so there is no PIPESTATUS),
        # and a run killed by the OOM killer prints neither FAILURE nor SORRY -- pack
        # would have gone on to announce a truncated ISO as "ok". The log is still
        # scanned because xorriso can report SORRY and exit 0.
        _log=$(mktemp)
        xorriso "$@" "$src" > "$_log" 2>&1 || _rc=$?
        _rc=${_rc:-0}
        if [ "$_rc" != 0 ] || grep -qiE 'FAILURE|SORRY' "$_log"; then
            grep -iE 'failure|sorry' "$_log" | head -5
            rm -f "$_log"
            die "pack: xorriso failed (exit $_rc)"
        fi
        rm -f "$_log"
        ;;
      *) die "pack: unknown backend '$backend' (want genisoimage or xorriso)" ;;
    esac

    _abs=$(cd "$(dirname "$out")" && pwd)/$(basename "$out")
    _mib=$(( $(stat -c%s "$out") / 1048576 ))
    printf '  %sok%s   wrote %s  (%s MiB)\n' "$G" "$O" "$_abs" "$_mib"
    [ "$backend" = xorriso ] && [ "$appid" != "$(printf '%s' "$appid" | tr a-z A-Z)" ] && \
        printf '  %snote: xorriso uppercased the application id to %s%s\n' "$Y" \
               "$(printf '%s' "$appid" | tr a-z A-Z)" "$O"

    if [ -n "$_sums" ]; then
        _sumfile="$out.$_sums"
        # Relative name inside the file so `sha256sum -c` works from the output dir
        # regardless of where the ISO was built.
        ( cd "$(dirname "$out")" && "${_sums}sum" "$(basename "$out")" ) > "$_sumfile" \
            || die "pack: ${_sums}sum failed"
        printf '  %sok%s   wrote %s\n' "$G" "$O" "$_sumfile"
        _key=$(sed -n 's/^checksums_sign: *//p' "$_hints" 2>/dev/null | head -1)
        if [ -n "$_key" ] && [ "$_key" != "false" ]; then
            if have gpg; then
                if gpg --batch --yes --local-user "$_key" \
                       --detach-sign --armor "$_sumfile" 2>/dev/null; then
                    printf '  %sok%s   signed %s.asc\n' "$G" "$O" "$_sumfile"
                else
                    printf '  %swarn%s gpg could not sign with key %s -- checksum written unsigned\n' \
                           "$Y" "$O" "$_key"
                fi
            else
                printf '  %swarn%s gpg not installed -- checksum written unsigned\n' "$Y" "$O"
            fi
        fi
    fi
    return 0
}
