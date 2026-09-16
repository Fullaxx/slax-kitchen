#!/bin/sh
# kitchen build  -- fetch/resolve -> unpack -> apply -> pack -> test, from one profile.
# kitchen test   -- run checks against an already-built ISO.

# Run the structural assertions. Flags are derived from what the profile asked for, so a
# profile that requests `hybrid: true` also gets asserted on -- a recipe that silently did
# not take effect is exactly what this is meant to catch.
_test_structure() {
    _iso=$1; _uefi=$2; _hybrid=$3
    set -- "$REPO_ROOT/tests/structure/iso_assert.py" "$_iso"
    [ -n "$_uefi" ] && set -- "$@" --expect-uefi
    [ -n "$_hybrid" ] && set -- "$@" --expect-hybrid
    python3 "$@"
}

# Derive the keystrokes that select a boot entry with console=ttyS on it, by reading the
# ISO's OWN menu rather than hardcoding a count. Prints a --keys spec, or fails if the
# image has no such entry -- in which case the caller keeps today's evidence-only boot.
#
# Counting arrow presses was the obvious approach and it is wrong twice over. The Slax
# isolinux menu sets MENU ROWS 5, so the sixth entry that `serial-console` appends is not
# even on screen; and two entries carry MENU DISABLED, which display but are skipped by
# navigation. Measured on the example ISO: six LABELs, four selectable, serial reachable
# in three presses rather than the five a LABEL count suggests.
#
# So for isolinux, do not navigate at all. Esc reveals the hidden menu and a SECOND Esc
# drops to the `boot:` prompt, where an entry can be typed BY NAME -- order-independent,
# MENU ROWS-independent, and self-describing. GRUB has no such prompt, but its generated
# menu has no disabled entries and no row limit, so counting menuentry lines is sound
# there. (The two menus differ: uefi-bootable omits the disabled session entries, so a
# shared count would have selected memtest under UEFI.)
_serial_keys() {
    _sk_iso=$1 _sk_mode=$2
    have xorriso || return 1
    _sk_d=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-menu.XXXXXX")
    if [ "$_sk_mode" = uefi ]; then
        _sk_cfg=/boot/grub/grub.cfg
    else
        _sk_cfg=/slax/boot/isolinux.cfg
    fi
    xorriso -osirrox on -indev "$_sk_iso" -extract "$_sk_cfg" "$_sk_d/cfg" -- \
        >/dev/null 2>&1
    [ -s "$_sk_d/cfg" ] || { rm -rf "$_sk_d"; return 1; }

    if [ "$_sk_mode" = uefi ]; then
        _sk_n=$(awk '/^menuentry /{i++} /console=ttyS/{print i-1; exit}' "$_sk_d/cfg")
        rm -rf "$_sk_d"
        [ -n "$_sk_n" ] || return 1
        # GRUB draws its menu immediately and any keypress stops the countdown.
        _sk_k="2s"; _sk_i=0
        while [ "$_sk_i" -lt "$_sk_n" ]; do _sk_k="$_sk_k,down"; _sk_i=$((_sk_i+1)); done
        printf '%s,ret\n' "$_sk_k"
        return 0
    fi

    _sk_lbl=$(awk '/^LABEL /{l=$2} /console=ttyS/{print l; exit}' "$_sk_d/cfg")
    rm -rf "$_sk_d"
    [ -n "$_sk_lbl" ] || return 1
    # Only a-z0-9 can be typed as qcodes without a translation table. Anything else and
    # we would be guessing at key names, so say we cannot rather than send nonsense.
    case "$_sk_lbl" in *[!a-z0-9]*) return 1 ;; esac
    _sk_k="1s,esc,0.5s,esc,0.5s"
    for _sk_c in $(printf '%s' "$_sk_lbl" | sed 's/./& /g'); do
        _sk_k="$_sk_k,$_sk_c"
    done
    printf '%s,ret\n' "$_sk_k"
}

kitchen_test() {
    iso="" want_structure=0 want_bios=0 want_uefi=0 want_kernel=0
    want_usb=0 want_perch=0
    expect_uefi="" expect_hybrid="" secs=32 expects=""
    keys="" auto_keys=1 mem="" golden="" record="" perchdev="/dev/sda"
    outdir_opt=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --structure)    want_structure=1; shift ;;
            --bios|--bios-boot) want_bios=1; shift ;;
            --uefi|--uefi-boot) want_uefi=1; shift ;;
            --kernel|--kernel-boot) want_kernel=1; shift ;;
            --usb|--usb-boot) want_usb=1; shift ;;
            --persistence)  want_perch=1; shift ;;
            --expect-uefi)  expect_uefi=1; shift ;;
            --expect-hybrid) expect_hybrid=1; shift ;;
            --seconds)      secs=$2; shift 2 ;;
            --keys)         keys=$2; auto_keys=0; shift 2 ;;
            --no-keys)      keys=""; auto_keys=0; shift ;;
            --mem)          mem=$2; shift 2 ;;
            --golden)       golden=$2; shift 2 ;;
            --record)       record=$2; shift 2 ;;
            --perch-device) perchdev=$2; shift 2 ;;
            # Evidence normally lands beside the ISO. An explicit --out lets a driver
            # keep a run's artifacts together, and lets someone test an ISO that lives
            # somewhere they cannot write.
            --out)          outdir_opt=$2; shift 2 ;;
            # Repeatable. Stored one per line because an expectation contains spaces.
            --expect)       expects="$expects$2
"; shift 2 ;;
            -h|--help)      usage_cmd test; return 0 ;;
            -*) die "test: unknown option $1" ;;
            *)  iso=$1; shift ;;
        esac
    done
    [ -n "$iso" ] || die "test: need an ISO path"
    [ -f "$iso" ] || die "test: no such file: $iso"
    [ "$want_structure" = 0 ] && [ "$want_bios" = 0 ] && [ "$want_uefi" = 0 ] \
        && [ "$want_kernel" = 0 ] && [ "$want_usb" = 0 ] && [ "$want_perch" = 0 ] \
        && want_structure=1
    outdir=${outdir_opt:-"$(dirname "$iso")/boot-tests"}
    mkdir -p "$outdir" || die "test: cannot write to $outdir"

    rc=0
    if [ "$want_structure" = 1 ]; then
        printf '%s* structure%s\n' "$B" "$O"
        _test_structure "$iso" "$expect_uefi" "$expect_hybrid" || rc=1
    fi

    if [ "$want_kernel$want_bios$want_uefi$want_usb$want_perch" != "00000" ]; then
        have qemu-system-x86_64 || { warn_no_qemu; return 1; }
        [ -r /dev/kvm ] || printf '  %snote: no /dev/kvm, running under TCG -- this is slow%s\n' \
            "$D" "$O"
    fi

    # Direct kernel boot and the persistence pair both need the kernel and initramfs
    # lifted out of the ISO, so extract once.
    kb=""
    if [ "$want_kernel" = 1 ] || [ "$want_perch" = 1 ]; then
        kb=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-kb.XXXXXX")
        if ! xorriso -osirrox on -indev "$iso" \
              -extract /slax/boot/vmlinuz "$kb/vmlinuz" \
              -extract /slax/boot/initrfs.img "$kb/initrfs.img" -- >/dev/null 2>&1 \
           || [ ! -s "$kb/vmlinuz" ] || [ ! -s "$kb/initrfs.img" ]; then
            printf '  %sFAIL%s could not extract vmlinuz/initrfs.img from %s\n' "$R" "$O" "$iso"
            rm -rf "$kb"; kb=""; rc=1; want_kernel=0; want_perch=0
        fi
    fi

    # Direct kernel boot. The menu modes cannot assert on serial unless they are pointed
    # at an entry that names ttyS0 as its LAST console= -- see _serial_keys above and
    # recipes/available/serial-console.yaml. This mode needs no such help: it builds the
    # cmdline itself, so the livekit markers are checkable and a failure names the stage
    # it stopped at.
    if [ "$want_kernel" = 1 ]; then
        printf '%s* kernel boot%s  %s(direct, bypasses the bootloader)%s\n' "$B" "$O" "$D" "$O"
        _boot_run kernel --kernel "$kb/vmlinuz" --initrd "$kb/initrfs.img" \
            --expect 'Looking for slax data' \
            --expect 'Mounting bundles' \
            --expect 'Live Kit done, starting slax' || rc=1
    fi

    # Bootloader paths. bios and uefi differ in firmware; usb differs in how the image is
    # attached -- as a usb-storage device rather than a CD, which is what a dd'd stick
    # looks like and therefore needs the isohybrid recipe.
    modes=""
    [ "$want_bios" = 1 ] && modes="$modes bios"
    [ "$want_uefi" = 1 ] && modes="$modes uefi"
    [ "$want_usb" = 1 ]  && modes="$modes usb"
    for mode in $modes; do
        mkeys=$keys
        if [ "$auto_keys" = 1 ]; then
            mkeys=$(_serial_keys "$iso" "$mode") || mkeys=""
        fi
        if [ -n "$mkeys" ]; then
            printf '%s* %s boot%s  %s(bootloader; selects the serial entry and asserts on it)%s\n' \
                "$B" "$mode" "$O" "$D" "$O"
            _boot_run "$mode" --keys "$mkeys" \
                --expect 'Looking for slax data' \
                --expect 'Mounting bundles' \
                --expect 'Live Kit done, starting slax' || rc=1
        else
            # No entry names ttyS0 last, so nothing this boot produces can be read. Say
            # so: "I could not check" and "I checked and it is fine" are different claims,
            # and for four CI runs this mode reported the second while meaning the first.
            printf '%s* %s boot%s  %s(bootloader; screenshot only -- no serial entry in this ISO)%s\n' \
                "$B" "$mode" "$O" "$Y" "$O"
            use_golden=0 _boot_run "$mode" || rc=1
        fi
    done

    # Persistence: the same writable device across two boots, asserting that a marker
    # written by the first survives into the second.
    #
    # Direct-kernel mode rather than the menu, deliberately. perch is a livekit-init
    # behaviour and the bootloader has nothing to do with it, while a menu boot would add
    # keystroke timing to a test that is already two boots long. It is also the only way
    # to put perchdir= on the cmdline without editing the image under test.
    if [ "$want_perch" = 1 ]; then
        printf '%s* persistence%s  %s(two boots, one disk: a marker must survive)%s\n' \
            "$B" "$O" "$D" "$O"
        perchdir="$outdir/perch/$(basename "$iso" .iso)"
        perchimg="$perchdir/perch.img"
        rm -rf "$perchdir"; mkdir -p "$perchdir"
        prc=0
        printf '  %sboot 1 of 2 -- expect the marker to be absent and get created%s\n' "$D" "$O"
        _boot_run kernel --kernel "$kb/vmlinuz" --initrd "$kb/initrfs.img" \
            --disk "$perchimg" --append "perchdir=$perchdev/slax/changes" \
            --run-tag perch1 --label persistence \
            --expect 'Live Kit done, starting slax' \
            --expect 'perch-marker: absent' || prc=1
        if [ "$prc" = 0 ]; then
            printf '  %sboot 2 of 2 -- the same disk; the marker must still be there%s\n' "$D" "$O"
            _boot_run kernel --kernel "$kb/vmlinuz" --initrd "$kb/initrfs.img" \
                --disk "$perchimg" --append "perchdir=$perchdev/slax/changes" \
                --run-tag perch2 --label persistence \
                --expect 'Live Kit done, starting slax' \
                --expect 'perch-marker: present' || prc=1
        fi
        if [ "$prc" = 0 ]; then
            # The disk exists only to carry state between the two boots. Keeping it would
            # also make the NEXT run start from a session that already has the marker,
            # which would pass without proving anything.
            rm -rf "$perchdir"
        else
            printf '  %skept %s (%s) -- it is the evidence%s\n' "$Y" "$perchimg" \
                "$(du -h "$perchimg" 2>/dev/null | cut -f1)" "$O"
            rc=1
        fi
    fi

    [ -n "$kb" ] && rm -rf "$kb"
    return $rc
}

# One invocation of the boot harness, carrying the options every caller shares. The
# caller's own --expect flags come first, then the user's from the command line.
_boot_run() {
    _br_mode=$1; shift
    # A pid file per boot, under the evidence directory. qemu_boot.py removes it on the
    # way out, so one still sitting there afterwards means an orphaned guest -- which is
    # exactly what a driver's trap needs to find and kill, and only its own.
    mkdir -p "$outdir/pids"
    set -- "$REPO_ROOT/tests/boot/qemu_boot.py" "$iso" --mode "$_br_mode" \
        --seconds "$secs" --out "$outdir" \
        --pidfile "$outdir/pids/$_br_mode-$$.pid" "$@"
    [ -n "$mem" ] && set -- "$@" --mem "$mem"
    [ -n "$record" ] && set -- "$@" --record "$record"
    # A golden diff reads the testkit block out of the serial log, so it is meaningless
    # on a boot that produces no serial output at all.
    [ -n "$golden" ] && [ "${use_golden:-1}" = 1 ] && set -- "$@" --golden "$golden"
    _oifs=$IFS; IFS='
'
    for _e in $expects; do [ -n "$_e" ] && set -- "$@" --expect "$_e"; done
    IFS=$_oifs
    python3 "$@"
}

warn_no_qemu() {
    printf '  %sqemu-system-x86_64 not installed -- skipping boot test%s\n' "$Y" "$O"
}

kitchen_build() {
    profile="" keep=0 skip_test=0 force=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --keep)      keep=1; shift ;;
            --no-test)   skip_test=1; shift ;;
            -f|--force)  force=1; shift ;;
            -h|--help)      usage_cmd build; return 0 ;;
            -*) die "build: unknown option $1" ;;
            *)  profile=$1; shift ;;
        esac
    done
    [ -n "$profile" ] || die "build: need a profile (see profiles/)"

    # Parse + validate the profile in one step; a bad profile must fail before any work.
    _vars=$(python3 "$REPO_ROOT/lib/profile.py" "$profile") || exit 1
    eval "$_vars"

    printf '%sbuild%s %s\n' "$B" "$O" "$PROFILE_NAME"
    printf '  base    %s %s %s\n' "$BASE_FLAVOUR" "$BASE_ARCH" "$BASE_VERSION"
    printf '  recipes %s\n' "$RECIPES"
    printf '  output  out/%s\n\n' "$OUTPUT_NAME"

    # Preflight BEFORE unpacking 400+ MiB. Facts come from the profile, so `when:`
    # guards resolve without needing a work tree that does not exist yet.
    if [ -n "$RECIPES" ]; then
        # --profile, not $RECIPES: the profile may attach per-recipe vars, which a
        # space-joined shell string cannot carry -- and unquoted word-splitting broke on
        # any recipe path containing a space.
        python3 "$REPO_ROOT/lib/apply.py" --profile "$PROFILE_PATH" --preflight-only \
            --facts "flavour=$BASE_FLAVOUR,arch=$BASE_ARCH" || exit 1
    fi
    # pack's own tools, checked here rather than after the build has run.
    _backend=${OUTPUT_BACKEND:-}
    if [ -z "$_backend" ]; then
        if [ -n "$OUTPUT_HYBRID" ]; then _backend=xorriso; else _backend=genisoimage; fi
    fi
    have "$_backend" || die "build: $_backend not installed (needed to write the ISO)"
    if [ -n "$OUTPUT_HYBRID" ] && [ ! -f /usr/lib/ISOLINUX/isohdpfx.bin ]; then
        die "build: /usr/lib/ISOLINUX/isohdpfx.bin missing (apt-get install isolinux) -- needed for --hybrid"
    fi
    echo

    if [ ! -f "$BASE_ISO" ]; then
        printf '%serror:%s base ISO not found: %s\n' "$R" "$O" "$BASE_ISO" >&2
        [ "$BASE_ISO_SOURCE" = derived ] && {
            printf '  the profile has no explicit `iso:`, so the path was derived from\n' >&2
            printf '  base.flavour/arch/version. Either drop the ISO there, or set `iso:`.\n' >&2
        }
        printf '  Download: https://ftp.linux.cz/pub/linux/slax/\n' >&2
        exit 1
    fi

    work="work/$PROFILE_NAME"
    if [ -e "$work" ] && [ "$force" != 1 ]; then
        die "build: $work exists (use --force to rebuild from scratch)"
    fi
    rm -rf "$work"

    kitchen_unpack "$BASE_ISO" -o "$work" || exit 1
    echo
    if [ -n "$RECIPES" ]; then
        python3 "$REPO_ROOT/lib/apply.py" --profile "$PROFILE_PATH" -w "$work" || exit 1
        echo
    fi

    set -- -s "$work/iso" -o "out/$OUTPUT_NAME"
    [ "$force" = 1 ] && set -- "$@" --force
    [ -n "$OUTPUT_BACKEND" ] && set -- "$@" --backend "$OUTPUT_BACKEND"
    [ -n "$OUTPUT_HYBRID" ] && set -- "$@" --hybrid
    kitchen_pack "$@" || exit 1

    rc=0
    if [ "$skip_test" != 1 ] && [ -n "$TESTS" ]; then
        echo
        # A profile asking for uefi-bootable/isohybrid should be asserted on having got
        # them, so derive the expectations from the recipe list rather than trusting it.
        _eu=""; _eh=""
        case " $RECIPES " in *" uefi-bootable "*) _eu=1 ;; esac
        case " $RECIPES " in *" isohybrid "*) _eh=1 ;; esac
        [ -n "$OUTPUT_HYBRID" ] && _eh=1
        for t in $TESTS; do
            case "$t" in
                structure) kitchen_test "out/$OUTPUT_NAME" --structure \
                             ${_eu:+--expect-uefi} ${_eh:+--expect-hybrid} || rc=1 ;;
                kernel-boot) kitchen_test "out/$OUTPUT_NAME" --kernel || rc=1 ;;
                bios-boot) kitchen_test "out/$OUTPUT_NAME" --bios || rc=1 ;;
                uefi-boot) kitchen_test "out/$OUTPUT_NAME" --uefi || rc=1 ;;
                usb)       kitchen_test "out/$OUTPUT_NAME" --usb || rc=1 ;;
                persistence) kitchen_test "out/$OUTPUT_NAME" --persistence || rc=1 ;;
                *) printf '  %sskip%s unknown test %s\n' "$Y" "$O" "$t" ;;
            esac
        done
    fi

    [ "$keep" = 1 ] || rm -rf "$work"
    echo
    if [ "$rc" = 0 ]; then
        printf '%sbuild ok%s  out/%s\n' "$G" "$O" "$OUTPUT_NAME"
    else
        printf '%sbuild produced an ISO but tests failed%s  out/%s\n' "$R" "$O" "$OUTPUT_NAME"
    fi
    [ "$keep" = 1 ] && printf '  %swork tree kept at %s%s\n' "$D" "$work" "$O"
    return $rc
}
