"""Sizes in whole MiB, rounded one way everywhere (#93).

    import units
    units.mib(os.path.getsize(iso))      # the nearest MiB, a half rounding up
    python3 lib/units.py FILE            # the same, for a shell script

WHY. Every line that printed a size in whole MiB did its own arithmetic, and they did not
agree. kitchen pack's `wrote ... (N MiB)`, the recipe matrix's `ok` line, Tier C's `image`
line, apply's `removed ...`, fetch, tools/qemu/boot.py's summary and the boot host divided by
1048576 and dropped the remainder. The structure check's `structure:` line, the one the
cookbook pages quote, rounded to the nearest. So the same image read one MiB less on one line
than on another, and a331979 found two figures in the docs taken from a line that rounded
down, beside a stock 416 that is rounded: remove-bundle's 336 was 337, and firmware-refresh's
469 was 470.

ONE COPY ELSEWHERE, ON PURPOSE. lib/boot_host.py has its own _mib(). That one file is sent to
the boot host and runs there as the agent, stdlib only, so it cannot import this.
tests/unit/test_boot_host.py fails if the two round any size differently.

NOT FOR A COUNTER. lib/fetch.py's live progress counts what has arrived, and rounds down on
purpose. This is for a size that is finished.
"""
from __future__ import annotations

import os
import sys

MIB = 1048576


def mib(n: int) -> int:
    """`n` bytes in whole MiB: the nearest, with a half rounding up."""
    return (n + MIB // 2) // MIB


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: units.py FILE   (prints FILE's size in whole MiB)", file=sys.stderr)
        raise SystemExit(2)
    print(mib(os.path.getsize(sys.argv[1])))
