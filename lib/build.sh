#!/bin/sh
# kitchen build  -- fetch/resolve -> unpack -> apply -> pack -> test, from one profile.
# kitchen test   -- run checks against an already-built ISO.

# Run the structural assertions. Flags are derived from what the profile asked for, so a
# profile that requests `hybrid: true` also gets asserted on -- a recipe that silently did
# not take effect is exactly what this is meant to catch.
_test_structure() {
    _iso=$1; _uefi=$2; _hybrid=$3; _volid=$4
    set -- "$REPO_ROOT/tests/structure/iso_assert.py" "$_iso"
    [ -n "$_uefi" ] && set -- "$@" --expect-uefi
    [ -n "$_hybrid" ] && set -- "$@" --expect-hybrid
    [ -n "$_volid" ] && set -- "$@" --volid "$_volid"
    python3 "$@"
}

# Derive the keystrokes that select a boot entry with console=ttyS on it, by reading the
# ISO's OWN menu rather than hardcoding a count. Prints a --keys spec and returns 0; or
# returns 1 when the menu was read and has no such entry, and the caller falls back to an
# evidence-only boot; or returns 2, having said why on stderr, when the menu could not be
# read or the entry cannot be selected.
#
# 1 AND 2 ARE DIFFERENT CLAIMS. They used to be one status, so a machine without xorriso, an
# image whose menu would not extract, and an entry whose label cannot be typed were all
# reported as "no serial entry in this ISO". The caller then booted with nothing to assert,
# and the boot passed on a screenshot while blaming the image.
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
    if ! have xorriso; then
        printf '  %scannot read the boot menu: xorriso is not installed (apt-get install xorriso)%s\n' \
            "$R" "$O" >&2
        return 2
    fi
    _sk_d=$(mktemp -d "${TMPDIR:-/tmp}/kitchen-menu.XXXXXX")
    if [ "$_sk_mode" = uefi ]; then
        _sk_cfg=/boot/grub/grub.cfg
    else
        _sk_cfg=/slax/boot/isolinux.cfg
    fi
    _sk_err=$(xorriso -osirrox on -indev "$_sk_iso" -extract "$_sk_cfg" "$_sk_d/cfg" -- \
        2>&1 >/dev/null)
    if [ ! -s "$_sk_d/cfg" ]; then
        rm -rf "$_sk_d"
        # "missing or empty": an empty menu extracts cleanly, with no FAILURE line to
        # explain it, and is no more a menu without the entry than a missing one is.
        printf '  %scannot read the boot menu: %s is missing or empty in %s%s\n' \
            "$R" "$_sk_cfg" "$(basename "$_sk_iso")" "$O" >&2
        # xorriso says why on a FAILURE line -- a missing file, or no ISO at all.
        printf '%s\n' "$_sk_err" | grep 'FAILURE' | head -2 | sed 's/^/    /' >&2
        [ "$_sk_mode" = uefi ] && printf '    %s\n' \
            "--uefi boots GRUB, whose menu the uefi-bootable recipe writes there" >&2
        return 2
    fi

    if [ "$_sk_mode" = uefi ]; then
        _sk_n=$(awk '/^menuentry /{i++} /console=ttyS/{print i-1; exit}' "$_sk_d/cfg")
        # DERIVED FROM THE IMAGE, not assumed. Any keypress stops GRUB's countdown -- but
        # only once GRUB exists, and OVMF under TCG spends about nine seconds in firmware
        # first: a screendump at 9 s is still blank and one at 14 s already shows the EFI
        # stub loading the kernel. So a two-second lead works under KVM and misses every
        # time under TCG, which is what a fixed number gets you.
        #
        # A fixed TEN is no better in the other direction, and I shipped it before finding
        # out: on an image with GRUB's default 5 s timeout, a 10 s lead arrives after the
        # menu has gone and the DEFAULT entry has booted. The comment here used to claim
        # that case was "merely slow under KVM". It is not slow, it is wrong -- the test
        # then asserts against a boot it never selected.
        #
        # So read the timeout out of the generated grub.cfg and stay inside it.
        _sk_t=$(awk -F= '/^set timeout=/{print $2; exit}' "$_sk_d/cfg")
        rm -rf "$_sk_d"
        [ -n "$_sk_n" ] || return 1
        : "${_sk_t:=5}"
        if [ "$_sk_t" -ge 15 ]; then
            _sk_k="10s"
        else
            # Too narrow to survive OVMF's firmware phase under TCG. Two seconds is right
            # under KVM; say plainly that it is a gamble anywhere slower, and name the
            # knob rather than leaving someone to rediscover this.
            _sk_k="2s"
            printf '  %snote: this image gives GRUB %ss. Under TCG, OVMF alone takes ~9 s,%s\n' \
                "$Y" "$_sk_t" "$O" >&2
            printf '  %s      so the menu may be missed and the DEFAULT entry booted. Raise it%s\n' \
                "$Y" "$O" >&2
            printf '  %s      with uefi-bootable'"'"'s menu_timeout (profiles/boot-matrix.yaml uses 30).%s\n' \
                "$Y" "$O" >&2
        fi
        _sk_i=0
        while [ "$_sk_i" -lt "$_sk_n" ]; do _sk_k="$_sk_k,down"; _sk_i=$((_sk_i+1)); done
        printf '%s,ret\n' "$_sk_k"
        return 0
    fi

    _sk_lbl=$(awk '/^LABEL /{l=$2} /console=ttyS/{print l; exit}' "$_sk_d/cfg")
    rm -rf "$_sk_d"
    [ -n "$_sk_lbl" ] || return 1
    # Only a-z0-9 can be typed as qcodes without a translation table. Anything else and
    # we would be guessing at key names, so say we cannot rather than send nonsense --
    # and say it as what it is. The image HAS a serial entry; it is this harness that
    # cannot reach it.
    case "$_sk_lbl" in
        *[!a-z0-9]*)
            printf '  %sthe serial entry is LABEL %s, and only a-z and 0-9 can be typed at the%s\n' \
                "$R" "$_sk_lbl" "$O" >&2
            printf '    boot: prompt -- rename the label, or pass --keys to select it yourself\n' >&2
            return 2 ;;
    esac
    _sk_k="1s,esc,0.5s,esc,0.5s"
    for _sk_c in $(printf '%s' "$_sk_lbl" | sed 's/./& /g'); do
        _sk_k="$_sk_k,$_sk_c"
    done
    printf '%s,ret\n' "$_sk_k"
}

# Hand the boot half of `kitchen test` to the boot host.
#
# THE FLAGS ARE REBUILT, NOT FORWARDED. kitchen_test's parser has already resolved them --
# `--uefi-boot` became want_uefi, an absent --seconds became 32 -- and passing "$@" through
# would send the boot host a second copy of the flags this side must keep for itself
# (--structure, --expect-uefi, --volid) while losing the defaults.
#
# It lives HERE, immediately above that parser, so that adding a boot flag to one and
# forgetting the other is visible in a single screen rather than being two files apart.
# Only the four path options are named; everything else is a tail lib/boot_host.py passes
# through to the copy of `kitchen` it runs over there.
_boot_remote() {
    set -- --iso "$iso" --out "$outdir"
    [ -n "$golden" ] && set -- "$@" --golden "$golden"
    [ -n "$record" ] && set -- "$@" --record "$record"
    set -- "$@" --
    [ "$want_kernel" = 1 ] && set -- "$@" --kernel
    [ "$want_bios" = 1 ]   && set -- "$@" --bios
    [ "$want_uefi" = 1 ]   && set -- "$@" --uefi
    [ "$want_usb" = 1 ]    && set -- "$@" --usb
    [ "$want_perch" = 1 ]  && set -- "$@" --persistence --perch-device "$perchdev"
    set -- "$@" --seconds "$secs"
    [ -n "$mem" ] && set -- "$@" --mem "$mem"
    # Only when the caller actually chose. auto_keys=1 means "read the image's own menu",
    # which is the default and is done over there, against the image that is over there.
    if [ "$auto_keys" = 0 ]; then
        if [ -n "$keys" ]; then set -- "$@" --keys "$keys"
        else set -- "$@" --no-keys; fi
    fi
    _oifs=$IFS; IFS='
'
    for _e in $expects; do [ -n "$_e" ] && set -- "$@" --expect "$_e"; done
    IFS=$_oifs
    python3 "$REPO_ROOT/lib/boot_host.py" test "$@"
}

kitchen_test() {
    iso="" want_structure=0 want_bios=0 want_uefi=0 want_kernel=0
    want_usb=0 want_perch=0
    expect_uefi="" expect_hybrid="" expect_volid="" secs=32 expects=""
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
            --volid)        expect_volid=$2; shift 2 ;;
            --seconds)      secs=$2; shift 2 ;;
            --keys)         keys=$2; auto_keys=0; shift 2 ;;
            --no-keys)      keys=""; auto_keys=0; shift ;;
            --mem)          mem=$2; shift 2 ;;
            --golden)       golden=$2; shift 2 ;;
            --record)       record=$2; shift 2 ;;
            --perch-device) perchdev=$2; shift 2 ;;
            # Boot here, whatever boot-host.ini says. Exported rather than kept in a
            # variable because everything downstream -- lib/boot_host.py, and the copy of
            # `kitchen` that a boot host runs inside its own run directory -- reads the
            # same environment variable, so there is one answer to the question.
            --local)        KITCHEN_BOOT_HOST=local; export KITCHEN_BOOT_HOST; shift ;;
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
    # AND THE KEY SPEC, ON THIS SIDE OF THE DISPATCH. A spec this harness cannot read
    # would otherwise be answered after the tree had been rsync'd to the boot host --
    # #31's lesson, "a missing image is answered here, not after the tree has been sent".
    # The grammar lives in qemu_boot.py and is ASKED here rather than copied: a second
    # copy in shell is how the two drift.
    if [ "$auto_keys" = 0 ] && [ -n "$keys" ]; then
        python3 "$REPO_ROOT/tests/boot/qemu_boot.py" --check-keys "$keys" || return 2
    fi
    [ "$want_structure" = 0 ] && [ "$want_bios" = 0 ] && [ "$want_uefi" = 0 ] \
        && [ "$want_kernel" = 0 ] && [ "$want_usb" = 0 ] && [ "$want_perch" = 0 ] \
        && want_structure=1
    outdir=${outdir_opt:-"$(dirname "$iso")/boot-tests"}
    mkdir -p "$outdir" || die "test: cannot write to $outdir"

    # WHERE THE BOOT MODES RUN. `boot_host.py active` answers with a status rather than
    # something to parse: 0 a boot host is configured and its name is on stdout, 1 boots
    # stay here, 2 the configuration is broken and has already been explained. Asked only
    # when a boot was actually requested, so `kitchen test --structure` -- which reads the
    # image that is already here -- never pays for it and never needs a host at all.
    #
    # The file first, and not only to save a python start on every boot test for the many
    # people who have no boot host. `python3 <missing file>` ALSO exits 2, which is the
    # code meaning "the configuration was refused" -- so without this gate a tree with no
    # lib/boot_host.py refuses to boot at all, citing a configuration that does not exist.
    # tests/unit/test_kitchen_test.py found that, being exactly such a tree.
    boot_host=""
    if [ "$want_kernel$want_bios$want_uefi$want_usb$want_perch" != "00000" ] \
       && [ -f "$REPO_ROOT/boot-host.ini" ] && [ -f "$REPO_ROOT/lib/boot_host.py" ]; then
        boot_host=$(python3 "$REPO_ROOT/lib/boot_host.py" active)
        _bh_rc=$?
        # Refused, not ignored. A broken boot-host.ini is a question for the person who
        # wrote it, and booting here instead would answer it by silently doing something
        # else -- see lib/boot_host.py's load().
        [ "$_bh_rc" = 2 ] && return 2
        [ "$_bh_rc" = 0 ] || boot_host=""
    fi

    # EVERY TOOL THE REQUESTED MODES NEED, checked before any mode runs. This was two partial
    # checks made mid-run: qemu, whose message said "skipping boot test" and then failed the
    # test, and xorriso for the boot modes only -- while --structure ran iso_assert.py, which
    # needs xorriso too, and died in a traceback without it. apply.py's VERB_REQUIRES
    # comment has the argument: checking on entry is "far too late".
    #   --structure                      xorriso lists the image's files
    #   --kernel, --persistence          qemu; xorriso takes the kernel out of the image;
    #                                    mkfs.ext4 makes the persistence disk
    #   --bios, --uefi, --usb            qemu; xorriso reads the menu, unless --keys or
    #                                    --no-keys means it is never read
    _need="python3"
    [ "$want_structure" = 1 ] && _need="$_need xorriso"
    if [ "$want_kernel$want_bios$want_uefi$want_usb$want_perch" != "00000" ]; then
        if [ -n "$boot_host" ]; then
            # The boot happens on another machine, so what is needed HERE is only what
            # carries it there. Asking for qemu would refuse to run a test that does not
            # need it -- on this container, every boot test. The modes' own tools are
            # checked on the boot host, by the boot host, before it starts.
            _need="$_need ssh rsync git"
        else
            _need="$_need qemu-system-x86_64"
            { [ "$want_kernel" = 1 ] || [ "$want_perch" = 1 ]; } && _need="$_need xorriso"
            [ "$want_perch" = 1 ] && _need="$_need mkfs.ext4"
            [ "$want_bios$want_uefi$want_usb" != 000 ] && [ "$auto_keys" = 1 ] \
                && _need="$_need xorriso"
        fi
    fi
    _miss=""
    for _t in $_need; do
        case " $_miss " in *" $_t "*) continue ;; esac
        have_tool "$_t" || _miss="$_miss $_t"
    done
    if [ -n "$_miss" ]; then
        for _t in $_miss; do
            printf '  %sFAIL%s %s is not installed (apt-get install %s)\n' \
                "$R" "$O" "$_t" "$(tool_pkg "$_t")"
        done
        printf '       nothing was tested: every mode asked for needs these before it can start\n'
        return 1
    fi

    rc=0
    if [ "$want_structure" = 1 ]; then
        printf '%s* structure%s\n' "$B" "$O"
        _test_structure "$iso" "$expect_uefi" "$expect_hybrid" "$expect_volid" || rc=1
    fi

    # THE ONE HOOK. Every boot in this toolkit funnels through the block below, so
    # diverting it here covers `kitchen build`'s profile tests, ci/tier-c.sh and CI without
    # any of them knowing that ssh exists.
    if [ -n "$boot_host" ] \
       && [ "$want_kernel$want_bios$want_uefi$want_usb$want_perch" != "00000" ]; then
        _boot_remote
        _brc=$?
        # 2 and 3 are NOT test results. "the configuration is wrong" and "the host could
        # not be reached" are answers about the machinery, and a caller that flattened
        # them to 1 would let ci/tier-c.sh write a ledger row for a boot that never
        # happened. They travel out of `kitchen test` unchanged.
        [ "$_brc" -ge 2 ] && return "$_brc"
        [ "$_brc" = 0 ] || rc=1
        return $rc
    fi

    if [ "$want_kernel$want_bios$want_uefi$want_usb$want_perch" != "00000" ]; then
        # -w, the test qemu_boot.py applies before it passes -enable-kvm. This was -r, so a
        # device this account could read and not write went to TCG with no note at all.
        if [ ! -e /dev/kvm ]; then
            printf '  %snote: no /dev/kvm, running under TCG -- this is slow%s\n' "$D" "$O"
        elif [ ! -w /dev/kvm ]; then
            printf '  %snote: /dev/kvm is not writable by this account, running under TCG -- this is slow%s\n' \
                "$D" "$O"
        fi
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
        mkeys=$keys why="--no-keys: the menu was not touched"
        if [ "$auto_keys" = 1 ]; then
            mkeys=$(_serial_keys "$iso" "$mode")
            case $? in
                0) ;;
                1) mkeys="" why="no serial entry in this ISO" ;;
                *)
                    # _serial_keys said why, above. Booting anyway would be a boot with
                    # nothing to assert, passing on a screenshot -- the defect this replaced.
                    printf '%s* %s boot%s  %sFAIL%s not booted: no entry could be chosen, for the reason above\n' \
                        "$B" "$mode" "$O" "$R" "$O"
                    rc=1
                    continue ;;
            esac
        fi
        if [ -n "$mkeys" ]; then
            printf '%s* %s boot%s  %s(bootloader; selects the serial entry and asserts on it)%s\n' \
                "$B" "$mode" "$O" "$D" "$O"
            _boot_run "$mode" --keys "$mkeys" \
                --expect 'Looking for slax data' \
                --expect 'Mounting bundles' \
                --expect 'Live Kit done, starting slax' || rc=1
        else
            # No entry names ttyS0 last, or the caller asked for the menu to be left alone,
            # so nothing this boot produces can be read. Say which: "I could not check" and
            # "I checked and it is fine" are different claims, and for four CI runs this mode
            # reported the second while meaning the first.
            printf '%s* %s boot%s  %s(bootloader; screenshot only -- %s)%s\n' \
                "$B" "$mode" "$O" "$Y" "$why" "$O"
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
    # NO PID FILE ANY MORE. Every boot used to write one here so that a driver could come
    # back afterwards, read the number and try to work out whether the process was still
    # alive and still ours. Nobody reads them now: ci/run-boot.py gives each boot a
    # process group and asks that instead, and lib/boot_host.py's agent does the same for
    # a remote one. The two questions the file existed to answer -- and the five defects
    # that came out of answering them badly -- went with it.
    set -- "$REPO_ROOT/tests/boot/qemu_boot.py" "$iso" --mode "$_br_mode" \
        --seconds "$secs" --out "$outdir" "$@"
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

kitchen_build() {
    # bld_ PREFIXED, and that is not style. These are POSIX sh functions with no
    # `local`, so every variable is global: kitchen_unpack (lib/unpack.sh:10) opens with
    # `iso="" dest="work" force=0`, which silently reset THIS function's `force` to 0 on
    # the way past. The result was that `kitchen build --force` could never overwrite an
    # existing ISO -- the work-tree half worked only because that check happens before
    # unpack is called -- while its help said "rebuild from scratch, overwriting the work
    # tree and ISO".
    profile="" keep=0 skip_test=0 bld_force=0 bld_base=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --keep)      keep=1; shift ;;
            --no-test)   skip_test=1; shift ;;
            -f|--force)  bld_force=1; shift ;;
            # Build a profile against a DIFFERENT base than the one it pins. A profile
            # describes a recipe set; which of the four targets to apply it to is a
            # property of the run. Without this, covering all four meant four near
            # identical profile files to keep in sync -- and the Tier C matrix covered
            # exactly the one target boot-matrix happened to name.
            --base)      bld_base=$2; shift 2 ;;
            # Reaches the profile's boot tests through the environment, because those go
            # through kitchen_test, which is the one place that decides where a boot runs.
            --local)     KITCHEN_BOOT_HOST=local; export KITCHEN_BOOT_HOST; shift ;;
            -h|--help)      usage_cmd build; return 0 ;;
            -*) die "build: unknown option $1" ;;
            *)  profile=$1; shift ;;
        esac
    done
    [ -n "$profile" ] || die "build: need a profile (see profiles/)"

    # Parse + validate the profile in one step; a bad profile must fail before any work.
    _vars=$(python3 "$REPO_ROOT/lib/profile.py" "$profile" ${bld_base:+--base "$bld_base"}) \
        || exit 1
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

    # Per TARGET, not per profile: building boot-matrix for all four targets in sequence
    # would otherwise have each unpack land on the last one's tree.
    work="work/$PROFILE_NAME-$BASE_FLAVOUR-$BASE_ARCH-$BASE_VERSION"
    if [ -e "$work" ] && [ "$bld_force" != 1 ]; then
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
    [ "$bld_force" = 1 ] && set -- "$@" --force
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
        # ...and the volume id is whatever the recipes asked pack for (iso-identity), read
        # through the same pack_hint pack used, before the work tree is removed below.
        _ev=$(pack_hint "$work/.kitchen/pack.yaml" volid)
        # bld_rc, for the same reason and with a worse symptom: kitchen_test opens with
        # `rc=0`, so a PASSING test reset the accumulator and erased a FAILING one before
        # it. A profile with `test: [structure, kernel-boot]` whose structure assertion
        # failed reported "build ok" as long as the boot passed.
        bld_rc=$rc
        for t in $TESTS; do
            _t_rc=0
            case "$t" in
                structure) kitchen_test "out/$OUTPUT_NAME" --structure \
                             ${_eu:+--expect-uefi} ${_eh:+--expect-hybrid} \
                             ${_ev:+--volid "$_ev"} || _t_rc=$? ;;
                kernel-boot) kitchen_test "out/$OUTPUT_NAME" --kernel || _t_rc=$? ;;
                bios-boot) kitchen_test "out/$OUTPUT_NAME" --bios || _t_rc=$? ;;
                uefi-boot) kitchen_test "out/$OUTPUT_NAME" --uefi || _t_rc=$? ;;
                usb)       kitchen_test "out/$OUTPUT_NAME" --usb || _t_rc=$? ;;
                persistence) kitchen_test "out/$OUTPUT_NAME" --persistence || _t_rc=$? ;;
                *) printf '  %sskip%s unknown test %s\n' "$Y" "$O" "$t" ;;
            esac
            # 2 and 3 mean the boot host could not be used, which is not a verdict on the
            # image. Running the remaining modes would ask the same unreachable machine the
            # same question four more times and report four more failures about it.
            if [ "$_t_rc" -ge 2 ]; then
                printf '  %scould not run %s on the boot host; the remaining tests were not attempted%s\n' \
                    "$R" "$t" "$O"
                bld_rc=$_t_rc
                break
            fi
            [ "$_t_rc" = 0 ] || bld_rc=1
        done
        rc=$bld_rc
    fi

    [ "$keep" = 1 ] || rm -rf "$work"
    echo
    if [ "$rc" = 0 ]; then
        printf '%sbuild ok%s  out/%s\n' "$G" "$O" "$OUTPUT_NAME"
    elif [ "$rc" -ge 2 ]; then
        # "the tests failed" would be a claim about the image, and nothing was learned
        # about the image. The ISO is fine and untested, and those are different states.
        printf '%sbuild produced an ISO; it could not be tested on the boot host%s  out/%s\n' \
            "$R" "$O" "$OUTPUT_NAME"
    else
        printf '%sbuild produced an ISO but tests failed%s  out/%s\n' "$R" "$O" "$OUTPUT_NAME"
    fi
    [ "$keep" = 1 ] && printf '  %swork tree kept at %s%s\n' "$D" "$work" "$O"
    return $rc
}
