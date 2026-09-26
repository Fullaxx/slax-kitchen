#!/bin/sh
# pack hints -- what recipes asked `kitchen pack` to do, read back.
#
# Recipes record mastering intent in <work>/.kitchen/pack.yaml (volid, uefi, hybrid, ...)
# because the mastering tool, not the tree, sets those fields. Three places read that file:
# pack itself, `kitchen build`'s structure test, and ci/recipe-matrix.sh. They each had
# their own sed line, and `kitchen build` had none -- so a profile with iso-identity packed
# volume id SLAX-CUSTOM and then failed its own structure test for not being 'slax'. One
# reader, sourced by all three, so what a test expects cannot drift from what pack did.
# base_boot_dropped, below, weighs what pack will write, hints and flags together, against
# what the unpacked image booted with.

# pack_hint <pack.yaml> <key>  -- the value, without surrounding quotes, or nothing.
# ONE LINE PER KEY: this reads the first line of a value and nothing else, so the writer
# (Ctx.hint in lib/apply.py) dumps YAML with folding switched off. A 128-character
# publisher folded at column 80 was mastered into an image cut short, and tested cut short.
pack_hint() {
    [ -f "$1" ] || return 0
    sed -n "s/^$2: *//p" "$1" | head -1 | sed "s/^[\"']//;s/[\"']$//"
}

# base_boot_dropped <origin.yaml> <uefi> <hybrid>  -- each way the unpacked image booted that
# this pack will not write, one sentence a line; <uefi> and <hybrid> are pack's own 0 or 1,
# once hints and flags have set them. Not a pack hint, but read the same way: origin.yaml is
# flat, and pack and tests/unit/test_unpack.py both source this file. The BIOS entry is not
# asked about, because pack always writes one. A key origin.yaml does not have -- a tree
# unpacked before unpack recorded them, or an image lib/isoparse.py could not read -- is no
# drop, because nothing says the image had it.
base_boot_dropped() {
    if [ "$(pack_hint "$1" boot_uefi)" = true ] && [ "$2" != 1 ]; then
        echo "the base image had a UEFI boot entry and this image will not: uefi-bootable" \
             "writes one, applied to the base's BIOS image (LAYERING.md, step 6)"
    fi
    if [ "$(pack_hint "$1" hybrid_mbr)" = true ] && [ "$3" != 1 ]; then
        echo "the base image had a hybrid MBR and this image will not: isohybrid writes one" \
             "(LAYERING.md, step 6)"
    fi
}
