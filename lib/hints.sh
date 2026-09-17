#!/bin/sh
# pack hints -- what recipes asked `kitchen pack` to do, read back.
#
# Recipes record mastering intent in <work>/.kitchen/pack.yaml (volid, uefi, hybrid, ...)
# because the mastering tool, not the tree, sets those fields. Three places read that file:
# pack itself, `kitchen build`'s structure test, and ci/recipe-matrix.sh. They each had
# their own sed line, and `kitchen build` had none -- so a profile with iso-identity packed
# volume id SLAX-CUSTOM and then failed its own structure test for not being 'slax'. One
# reader, sourced by all three, so what a test expects cannot drift from what pack did.

# pack_hint <pack.yaml> <key>  -- the value, without surrounding quotes, or nothing.
# ONE LINE PER KEY: this reads the first line of a value and nothing else, so the writer
# (Ctx.hint in lib/apply.py) dumps YAML with folding switched off. A 128-character
# publisher folded at column 80 was mastered into an image cut short, and tested cut short.
pack_hint() {
    [ -f "$1" ] || return 0
    sed -n "s/^$2: *//p" "$1" | head -1 | sed "s/^[\"']//;s/[\"']$//"
}
