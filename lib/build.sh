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

kitchen_test() {
    iso="" want_structure=0 want_bios=0 want_uefi=0 want_kernel=0
    expect_uefi="" expect_hybrid="" secs=32 expects=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --structure)    want_structure=1; shift ;;
            --bios|--bios-boot) want_bios=1; shift ;;
            --uefi|--uefi-boot) want_uefi=1; shift ;;
            --kernel|--kernel-boot) want_kernel=1; shift ;;
            --expect-uefi)  expect_uefi=1; shift ;;
            --expect-hybrid) expect_hybrid=1; shift ;;
            --seconds)      secs=$2; shift 2 ;;
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
        && [ "$want_kernel" = 0 ] && want_structure=1

    rc=0
    if [ "$want_structure" = 1 ]; then
        printf '%s* structure%s\n' "$B" "$O"
        _test_structure "$iso" "$expect_uefi" "$expect_hybrid" || rc=1
    fi
    # Direct kernel boot. The menu modes cannot assert on serial: their default entry
    # carries no console=ttyS0, so everything after the loader goes to video and the
    # serial log stays empty -- which is how a boot test that asserted nothing passed
    # for four CI runs. This mode puts the kernel on ttyS0 by construction, so the
    # livekit markers are checkable and a failure names the stage it stopped at.
    if [ "$want_kernel" = 1 ]; then
        have qemu-system-x86_64 || { warn_no_qemu; rc=1; want_kernel=0; }
    fi
    if [ "$want_kernel" = 1 ]; then
        printf '%s* kernel boot%s  %s(direct, bypasses the bootloader)%s\n' "$B" "$O" "$D" "$O"
        [ -r /dev/kvm ] || printf '  %snote: no /dev/kvm, running under TCG -- this is slow%s\n' "$D" "$O"
        kb=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-kb.XXXXXX")
        if xorriso -osirrox on -indev "$iso" \
              -extract /slax/boot/vmlinuz "$kb/vmlinuz" \
              -extract /slax/boot/initrfs.img "$kb/initrfs.img" -- >/dev/null 2>&1 \
           && [ -s "$kb/vmlinuz" ] && [ -s "$kb/initrfs.img" ]; then
            set -- "$REPO_ROOT/tests/boot/qemu_boot.py" "$iso" --mode kernel \
                --kernel "$kb/vmlinuz" --initrd "$kb/initrfs.img" \
                --seconds "$secs" --out "$(dirname "$iso")/boot-tests" \
                --expect 'Looking for slax data' \
                --expect 'Mounting bundles' \
                --expect 'Live Kit done, starting slax'
            if [ -n "$expects" ]; then
                _oifs=$IFS; IFS='
'
                for _e in $expects; do [ -n "$_e" ] && set -- "$@" --expect "$_e"; done
                IFS=$_oifs
            fi
            python3 "$@" || rc=1
        else
            printf '  %sFAIL%s could not extract vmlinuz/initrfs.img from %s\n' "$R" "$O" "$iso"
            rc=1
        fi
        rm -rf "$kb"
    fi

    modes=""
    [ "$want_bios" = 1 ] && modes="$modes bios"
    [ "$want_uefi" = 1 ] && modes="$modes uefi"
    for mode in $modes; do
        have qemu-system-x86_64 || { warn_no_qemu; rc=1; continue; }
        printf '%s* %s boot%s  %s(bootloader; asserts on the screenshot)%s\n' "$B" "$mode" "$O" "$D" "$O"
        if [ ! -r /dev/kvm ]; then
            printf '  %snote: no /dev/kvm, running under TCG -- this is slow%s\n' "$D" "$O"
        fi
        python3 "$REPO_ROOT/tests/boot/qemu_boot.py" "$iso" --mode "$mode" \
            --seconds "$secs" --out "$(dirname "$iso")/boot-tests" || rc=1
    done
    return $rc
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
        # shellcheck disable=SC2086
        python3 "$REPO_ROOT/lib/apply.py" $RECIPES --preflight-only \
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
        # shellcheck disable=SC2086
        python3 "$REPO_ROOT/lib/apply.py" $RECIPES -w "$work" || exit 1
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
                bios-boot) kitchen_test "out/$OUTPUT_NAME" --bios || rc=1 ;;
                uefi-boot) kitchen_test "out/$OUTPUT_NAME" --uefi || rc=1 ;;
                usb|persistence)
                    printf '  %sskip%s %s -- not implemented yet (see HOST_TASKS)\n' "$Y" "$O" "$t" ;;
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
