"""The tools a command needs, checked before it does anything.

    import need
    need.require(["xorriso"], "kitchen diff")   # prints what is missing and exits 2

WHY. The commands that WRITE an image already check first -- unpack, pack, build,
apply's preflight (VERB_REQUIRES, whose comment calls checking on entry "far too late"),
ci/tier-c.sh, tools/qemu/boot.py. The commands that only READ one did not: `kitchen test
--structure`, `diff`, `sources`, `probe` and `fingerprint` ran xorriso, unsquashfs, xz,
cpio and `file` unchecked, so a missing one surfaced as a Python traceback from deep
inside, naming a line rather than the package that fixes it.

NAMED need.py, NOT tools.py: the repository root has a tools/ directory, which Python
would import as a namespace package under that name whenever the root came first on
sys.path.
"""
from __future__ import annotations

import os
import shutil
import sys

# Which package puts each tool on PATH -- for the message, so it names the fix. Moved here
# from lib/apply.py, whose preflight uses it unchanged. kitchen's TOOLS table (shell, for
# doctor) is the other copy, and tests/unit/test_need.py fails if the two name different
# packages for a tool they both list.
TOOL_PKG = {
    "xorriso": "xorriso", "genisoimage": "genisoimage",
    "mksquashfs": "squashfs-tools", "unsquashfs": "squashfs-tools",
    "cpio": "cpio", "xz": "xz-utils",
    "grub-mkstandalone": "grub-efi-amd64-bin", "mkfs.vfat": "dosfstools",
    "mmd": "mtools", "mcopy": "mtools",
    "isohybrid": "syslinux-utils", "extlinux": "extlinux", "syslinux": "syslinux",
    "qemu-system-x86_64": "qemu-system-x86", "chroot": "coreutils",
    "file": "file", "git": "git", "mkfs.ext4": "e2fsprogs",
    # Only a boot host needs these, and only on the machine driving one; kitchen test
    # asks for them instead of qemu when boot-host.ini is in play.
    "ssh": "openssh-client", "rsync": "rsync",
}

# mkfs.* lives in /usr/sbin, which a non-login PATH -- an ssh command, a CI step -- can
# lack. qemu_boot.py and ci/tier-c.sh already look there; so does this, or a tool that is
# installed would be reported missing.
SBIN = ("/usr/sbin", "/sbin", "/usr/local/sbin")


def which(tool: str) -> str | None:
    found = shutil.which(tool)
    if found is None and tool.startswith("mkfs."):
        found = shutil.which(tool, path=os.pathsep.join(SBIN))
    return found


def missing(tools) -> list[str]:
    """One line per tool that is not installed, naming its package where it is known."""
    out = []
    for tool in dict.fromkeys(tools):          # each once, in the order asked
        if which(tool) is None:
            pkg = TOOL_PKG.get(tool)
            out.append(f"{tool} is not installed" + (f" (apt-get install {pkg})" if pkg else ""))
    return out


def require(tools, who: str) -> None:
    """Refuse -- exit 2, before any work -- if any of `tools` is not installed.

    Exit 2 because a refusal is an answer, not a crash: the same status qemu_boot.py and
    tools/qemu/boot.py give when they decline to start.
    """
    problems = missing(tools)
    if problems:
        for p in problems:
            print(f"{who}: {p}", file=sys.stderr)
        raise SystemExit(2)
