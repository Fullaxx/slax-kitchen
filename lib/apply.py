#!/usr/bin/env python3
"""Apply recipes to an unpacked Slax work tree.

A recipe is a list of steps; each step names a verb. Verbs are small, composable
operations on the work tree -- they never touch the source ISO, and they never build
an ISO themselves. `kitchen pack` does that afterwards.

Two things a verb may do besides editing files:

  * record a *pack hint* in <work>/.kitchen/pack.yaml -- e.g. boot.uefi builds an ESP
    and then asks pack to add the El Torito alt-boot entry. The recipe declares intent;
    pack honours it. This keeps recipes from having to know xorriso flags.

  * append to <work>/.kitchen/journal.yaml -- what ran, in order, with the recipe that
    asked for it. `kitchen probe` differences can then be traced back to a recipe.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import validate_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERBS: dict = {}

# Verbs the schema accepts but that will never be implemented, and why. They stay in
# the schema so that a recipe using one gets this explanation instead of a bare "not a
# valid verb" -- the question "why can't I do this?" deserves an answer at the point it
# is asked, not only in a doc the reader has not found yet.
WONT_DO = {
    "initramfs.config": (
        "only LIVEKITNAME and BEXT of the eight variables in /lib/config are read at "
        "runtime, and LIVEKITNAME is merely the default for from= (livekitlib:640), so "
        "renaming buys nothing and costs the from=...iso and PXE paths -- on CD it also "
        "needs isolinux.bin re-patched. Use the from= boot parameter instead."),
    "boot.secureboot": (
        "signing needs a key enrolled in the firmware's db, which this toolkit cannot "
        "do for you, and Slax's kernel is unsigned besides. Enroll your own key via MOK "
        "and sign the built ISO out of band."),
}

# Which Debian/Ubuntu package provides each tool, so a failure can say what to install
# rather than just what is absent.
TOOL_PKG = {
    "xorriso": "xorriso", "genisoimage": "genisoimage",
    "mksquashfs": "squashfs-tools", "unsquashfs": "squashfs-tools",
    "cpio": "cpio", "xz": "xz-utils",
    "grub-mkstandalone": "grub-efi-amd64-bin", "mkfs.vfat": "dosfstools",
    "mmd": "mtools", "mcopy": "mtools",
    "isohybrid": "syslinux-utils", "extlinux": "extlinux", "syslinux": "syslinux",
    "qemu-system-x86_64": "qemu-system-x86", "chroot": "coreutils",
}

# What each verb needs BEFORE it runs. `tools` are executables on PATH, `files` are
# data files a package must have installed, `caps` are kernel capabilities, and
# `network` means the step reaches the internet.
#
# This exists because verbs used to check their own tools on entry, which is far too
# late: applying four recipes with grub-mkstandalone missing downloaded a memtest
# binary, edited two bootloader configs and wrote a pack hint before dying on the
# fourth, leaving a half-modified work tree to clean up by hand.
VERB_REQUIRES: dict[str, dict] = {
    "boot.uefi": {"tools": ["grub-mkstandalone", "mkfs.vfat", "mmd", "mcopy"]},
    "boot.isohybrid": {"files": {"/usr/lib/ISOLINUX/isohdpfx.bin": "isolinux"}},
    "boot.grub": {"tools": ["grub-script-check"]},
    "bundle.packages": {"tools": ["unsquashfs", "mksquashfs", "chroot"],
                        "caps": ["chroot", "mknod"], "network": True},
    "bundle.fromDir": {"tools": ["mksquashfs"]},
    "bundle.files": {"tools": ["mksquashfs"]},
    "bundle.script": {"tools": ["unsquashfs", "mksquashfs", "chroot"],
                      "caps": ["chroot", "mknod"]},
    "bundle.fromTarball": {"tools": ["mksquashfs"]},
    "initramfs.files": {"tools": ["cpio", "xz"], "caps": ["mknod"]},
    "initramfs.modules": {"tools": ["cpio", "xz", "unsquashfs"], "caps": ["mknod"]},
    "initramfs.patch": {"tools": ["cpio", "xz"], "caps": ["mknod"]},
    "initramfs.busybox": {"tools": ["cpio", "xz"], "caps": ["mknod"]},
}


def _have_cap(cap: str) -> bool:
    """Probe a kernel capability by using it, not by parsing CapEff.

    Container runtimes can present a capability that seccomp then blocks, so the only
    honest answer comes from trying.
    """
    import tempfile
    if cap == "chroot":
        return subprocess.run(["chroot", "/", "/bin/true"],
                              capture_output=True).returncode == 0
    if cap == "mknod":
        d = tempfile.mkdtemp()
        try:
            os.mknod(os.path.join(d, "n"), 0o600 | 0o020000, os.makedev(1, 3))
            return True
        except OSError:
            return False
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return True


def step_requires(step: dict) -> dict:
    """Requirements for one step, including the ones that depend on its arguments."""
    req = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
           for k, v in VERB_REQUIRES.get(step.get("verb", ""), {}).items()}
    # boot.payload only touches the network when its source is a URL.
    if step.get("verb") == "boot.payload" and re.match(r"^https?://", str(step.get("src", ""))):
        req["network"] = True
    return req


def preflight(plan: list[tuple[str, dict]], check_network: bool = True) -> list[str]:
    """Check everything the whole plan needs, before any of it runs.

    `plan` is (recipe_name, step) pairs. Returns human-readable problems; empty is good.
    Reports EVERY missing thing at once -- fixing them one round-trip at a time is the
    thing that makes a late failure annoying.
    """
    need_tools: dict[str, set] = {}
    need_files: dict[str, tuple] = {}
    need_caps: dict[str, set] = {}
    need_net: set = set()

    for recipe, step in plan:
        req = step_requires(step)
        for t in req.get("tools", []):
            need_tools.setdefault(t, set()).add(recipe)
        for f, pkg in (req.get("files") or {}).items():
            need_files[f] = (pkg, recipe)
        for c in req.get("caps", []):
            need_caps.setdefault(c, set()).add(recipe)
        if req.get("network"):
            need_net.add(recipe)

    problems = []
    for tool in sorted(need_tools):
        if not shutil.which(tool):
            pkg = TOOL_PKG.get(tool)
            who = ", ".join(sorted(need_tools[tool]))
            hint = f"  (apt-get install {pkg})" if pkg else ""
            problems.append(f"missing tool '{tool}' needed by {who}{hint}")
    for f in sorted(need_files):
        if not os.path.exists(f):
            pkg, who = need_files[f]
            problems.append(f"missing file '{f}' needed by {who}  (apt-get install {pkg})")
    for cap in sorted(need_caps):
        if not _have_cap(cap):
            who = ", ".join(sorted(need_caps[cap]))
            problems.append(
                f"no {cap} capability, needed by {who} -- this container/host cannot "
                f"run it (see docs/40-workflow/container-vs-host.md)")
    if need_net and check_network:
        try:
            import socket
            socket.setdefaulttimeout(5)
            socket.getaddrinfo("deb.debian.org", 80)
        except OSError:
            problems.append("no network, needed by " + ", ".join(sorted(need_net)))
    return problems


def verb(name: str):
    def deco(fn):
        VERBS[name] = fn
        return fn
    return deco


class Ctx:
    """Everything a verb needs: where the tree is, and how to talk to the user."""

    def __init__(self, work: str, recipe_dir: str, recipe_name: str, dry: bool = False):
        self.work = work                       # <work>
        self.tree = os.path.join(work, "iso")  # <work>/iso
        self.facts: dict = {}                  # flavour/arch, for `when:` conditions
        self.meta = os.path.join(work, ".kitchen")
        self.recipe_dir = recipe_dir
        self.recipe = recipe_name
        self.dry = dry
        self.changes: list[str] = []
        os.makedirs(self.meta, exist_ok=True)

    def p(self, *parts) -> str:
        return os.path.join(self.tree, *parts)

    def say(self, msg: str) -> None:
        print(f"    {msg}")
        self.changes.append(msg)

    def hint(self, key: str, value) -> None:
        """Ask `kitchen pack` to do something at mastering time."""
        path = os.path.join(self.meta, "pack.yaml")
        import yaml
        cur = {}
        if os.path.isfile(path):
            cur = yaml.safe_load(open(path)) or {}
        cur[key] = value
        if not self.dry:
            with open(path, "w") as f:
                yaml.safe_dump(cur, f, sort_keys=True)
        self.say(f"pack hint: {key}={value}")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def subst(obj, vars_: dict):
    """Replace {{name}} in every string, recursively."""
    if isinstance(obj, str):
        def rep(m):
            k = m.group(1).strip()
            if k not in vars_:
                raise KeyError(f"undefined variable {{{{{k}}}}}")
            return str(vars_[k])
        return re.sub(r"\{\{([^}]+)\}\}", rep, obj)
    if isinstance(obj, dict):
        return {k: subst(v, vars_) for k, v in obj.items()}
    if isinstance(obj, list):
        return [subst(v, vars_) for v in obj]
    return obj


# ------------------------------------------------------------------ verbs ----

def _extract_member(archive: str, member: str, dest: str) -> None:
    """Pull one file out of a .zip or .tar.* without unpacking the rest.

    Type is detected from the CONTENT, not the filename: a download lands in a temp file
    named after its destination (memtest.bin.part), so a name-based check would send a
    zip to the tar reader.
    """
    with open(archive, "rb") as fh:
        magic = fh.read(6)
    if magic[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        import zipfile
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            hit = member if member in names else next(
                (n for n in names if os.path.basename(n) == member), None)
            if hit is None:
                raise RuntimeError(f"{member!r} not in archive (has: {', '.join(names[:10])})")
            with z.open(hit) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
        return
    import tarfile
    with tarfile.open(archive) as t:
        names = t.getnames()
        hit = member if member in names else next(
            (n for n in names if os.path.basename(n) == member), None)
        if hit is None:
            raise RuntimeError(f"{member!r} not in archive (has: {', '.join(names[:10])})")
        f = t.extractfile(hit)
        if f is None:
            raise RuntimeError(f"{member!r} is not a regular file")
        with open(dest, "wb") as out:
            shutil.copyfileobj(f, out)


@verb("boot.payload")
def v_boot_payload(ctx: Ctx, step: dict) -> None:
    """Put a file into slax/boot/ -- a memtest binary, a .c32 module, an EFI blob.

    `extract:` pulls a single member out of a downloaded .zip/.tar.*, because upstreams
    often publish only archives. The sha256 is checked against the ARCHIVE, which is what
    the publisher actually signs off on, before anything is taken out of it.
    """
    dest = _under(ctx.tree, step["dest"], "boot.payload")
    src = step.get("src")
    want = step.get("sha256")
    member = step.get("extract")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if ctx.dry:
        via = f" (extract {member})" if member else ""
        ctx.say(f"would install {step['dest']} from {src}{via}")
        return
    if src and re.match(r"^https?://", src):
        tmp = dest + ".part"
        with urllib.request.urlopen(src, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        got = sha256(tmp)
        if want and got != want:
            os.unlink(tmp)
            raise RuntimeError(f"{step['dest']}: sha256 mismatch\n  want {want}\n  got  {got}")
        if member:
            _extract_member(tmp, member, dest)
            os.unlink(tmp)
            ctx.say(f"extracted {member} from {os.path.basename(src)}")
        else:
            os.replace(tmp, dest)
    elif src:
        local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
        if not os.path.isfile(local):
            raise RuntimeError(f"boot.payload: source not found: {local}")
        got = sha256(local)
        if want and got != want:
            raise RuntimeError(f"{step['dest']}: sha256 mismatch\n  want {want}\n  got  {got}")
        if member:
            _extract_member(local, member, dest)
        else:
            shutil.copy2(local, dest)
    if "mode" in step:
        os.chmod(dest, int(step["mode"], 8))
    ctx.say(f"installed {step['dest']} ({os.path.getsize(dest)} bytes)")


@verb("iso.files")
def v_iso_files(ctx: Ctx, step: dict) -> None:
    for spec in step["files"]:
        dest = _under(ctx.tree, spec["dest"], "iso.files")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if ctx.dry:
            ctx.say(f"would write {spec['dest']}")
            continue
        if "content" in spec:
            with open(dest, "w") as f:
                f.write(spec["content"])
        else:
            src = spec["src"]
            local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
            if os.path.isdir(local):
                shutil.copytree(local, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(local, dest)
        if "mode" in spec and os.path.isfile(dest):
            os.chmod(dest, int(spec["mode"], 8))
        ctx.say(f"wrote {spec['dest']}")


# --------------------------------------------------------- initramfs --------
#
# initrfs.img is an xz'd SVR4/newc cpio. Editing it is open, change, close -- but three
# details are load-bearing and every one of them fails silently:
#
#   1. xz MUST use --check=crc32. The kernel's built-in xz decoder does not implement
#      CRC64, which is xz's default. Get this wrong and the machine reboot-loops with no
#      message, because the thing that would report the error is inside the archive that
#      failed to decompress.
#   2. The archive holds seven real device nodes. A non-root cpio silently turns them
#      into empty regular files, and the result cannot open its own console.
#   3. The module directory is version-specific: lib/modules/6.1.38 on 64-bit but
#      6.1.38-smp on 32-bit. Anything hardcoding the former breaks half the targets.
#
# Upstream's own pack command has no `sort`, so archive order follows readdir order and
# is not stable -- on overlayfs, five runs over an unchanged tree gave five orders. We
# add `LC_ALL=C sort`, which changes nothing at boot (cpio order is irrelevant to
# extraction) and makes a repack diffable.

INITRAMFS_REL = os.path.join("slax", "boot", "initrfs.img")


def _initramfs_unpack(ctx: "Ctx", workdir: str) -> str:
    """Extract initrfs.img into workdir/tree and return that path."""
    img = ctx.p("slax", "boot", "initrfs.img")
    if not os.path.isfile(img):
        raise RuntimeError(f"initramfs: {img} missing -- is this a Slax tree?")
    tree = os.path.join(workdir, "tree")
    os.makedirs(tree, exist_ok=True)
    r = subprocess.run(f"xz -dc {shlex.quote(img)} | cpio -id --quiet",
                       shell=True, cwd=tree, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"initramfs unpack failed: {r.stderr.strip()[:300]}")
    nodes = sum(1 for dirpath, _, files in os.walk(tree) for f in files
                if _is_dev_node(os.path.join(dirpath, f)))
    if nodes == 0:
        raise RuntimeError(
            "initramfs unpack produced no device nodes -- cpio ran without CAP_MKNOD. "
            "The tree is not repackable; run as root.")
    files = sum(len(f) for _, _, f in os.walk(tree))
    dirs = sum(len(d) for _, d, _ in os.walk(tree))
    ctx.say(f"unpacked initrfs.img ({files:,} files, {dirs:,} dirs, "
            f"{nodes} device nodes preserved)")
    return tree


def _is_dev_node(path: str) -> bool:
    import stat as _stat
    try:
        m = os.lstat(path).st_mode
    except OSError:
        return False
    return _stat.S_ISBLK(m) or _stat.S_ISCHR(m)


def _initramfs_module_dir(tree: str) -> str:
    """lib/modules/<release>, read from the tree -- never hardcoded."""
    base = os.path.join(tree, "lib", "modules")
    dirs = [d for d in sorted(os.listdir(base)) if os.path.isdir(os.path.join(base, d))] \
        if os.path.isdir(base) else []
    if not dirs:
        raise RuntimeError(f"initramfs: no lib/modules/<release> in {tree}")
    if len(dirs) > 1:
        raise RuntimeError(f"initramfs: several module dirs {dirs}; refusing to guess")
    return os.path.join(base, dirs[0])


def _initramfs_pack(ctx: "Ctx", tree: str) -> None:
    """Repack tree over slax/boot/initrfs.img, with upstream's exact parameters."""
    img = ctx.p("slax", "boot", "initrfs.img")
    before = os.path.getsize(img)
    tmp = img + ".new"
    cmd = ("find . -print | LC_ALL=C sort | cpio -o -H newc --quiet "
           f"| xz -T0 -f --extreme --check=crc32 > {shlex.quote(tmp)}")
    r = subprocess.run(cmd, shell=True, cwd=tree, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        raise RuntimeError(f"initramfs pack failed: {r.stderr.strip()[:300]}")

    # Never ship an image the kernel cannot decompress. xz reports the check type, so
    # this is cheap and catches the one mistake that produces a silent reboot loop.
    chk = subprocess.run(["xz", "--list", tmp], capture_output=True, text=True)
    if "CRC32" not in chk.stdout:
        os.unlink(tmp)
        raise RuntimeError("initramfs pack produced a non-CRC32 image; the kernel's xz "
                           "decoder cannot read it. Refusing to ship it.")
    os.replace(tmp, img)
    after = os.path.getsize(img)
    ctx.say(f"repacked initrfs.img  {before:,} -> {after:,} bytes ({after - before:+,})")
    ctx.changes.append("slax/boot/initrfs.img")


@verb("initramfs.files")
def v_initramfs_files(ctx: Ctx, step: dict) -> None:
    """Add files to the initramfs -- static binaries, scripts, config.

    Anything executable placed here must be STATIC: the initramfs carries no dynamic
    loader, so a dynamically linked binary fails with a bare "not found" that names the
    interpreter, not the binary. i386 is the safe choice because every one of the eight
    shipped binaries is i386 even on the 64-bit images, which is why the kernel is built
    with CONFIG_IA32_EMULATION. An x86-64 static binary works on the 64-bit targets only.

    Both are checked and warned about rather than refused -- a data file is a legitimate
    thing to add, and so is a deliberately 64-bit-only tool.
    """
    files = step.get("files") or []
    if not files:
        raise RuntimeError("initramfs.files: no files listed")
    if ctx.dry:
        for spec in files:
            ctx.say(f"would add {spec['dest']} to initrfs.img")
        return

    import tempfile
    work = tempfile.mkdtemp(prefix="kitchen-irfs-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    try:
        tree = _initramfs_unpack(ctx, work)
        for spec in files:
            rel = spec["dest"].lstrip("/")
            dest = _under(tree, rel, "initramfs.files")
            os.makedirs(os.path.dirname(dest), exist_ok=True)

            # Never let an added file clobber blkid or eject. They are real binaries
            # shadowing busybox applets of the same name, and livekitlib parses
            # `blkid -o full`, an option busybox's applet does not have. Overwriting one
            # breaks device detection at boot with no diagnostic.
            if os.path.basename(rel) in ("blkid", "eject") and os.path.exists(dest) \
                    and not step.get("force"):
                raise RuntimeError(
                    f"initramfs.files: {rel} shadows a standalone binary livekitlib "
                    f"depends on. Pass force: true if you really mean to replace it.")

            if "content" in spec:
                with open(dest, "w") as f:
                    f.write(spec["content"])
            else:
                src = spec["src"]
                local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
                if not os.path.isfile(local):
                    raise RuntimeError(f"initramfs.files: no such source file: {local}")
                shutil.copy2(local, dest)
            if "mode" in spec:
                os.chmod(dest, int(str(spec["mode"]), 8))

            note = _elf_note(dest)
            ctx.say(f"initramfs: + {spec['dest']}{note}")
        _initramfs_pack(ctx, tree)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _elf_note(path: str) -> str:
    """Describe an ELF's arch and linkage, so a bad addition is obvious in the log."""
    if not shutil.which("file"):
        return ""
    r = subprocess.run(["file", "-bL", path], capture_output=True, text=True)
    d = r.stdout.strip()
    if "ELF" not in d:
        return ""
    bits = "i386" if "80386" in d or "Intel 80386" in d else \
           "x86-64" if "x86-64" in d else "?"
    if "dynamically linked" in d:
        return f"  [{bits}, DYNAMIC -- the initramfs has no loader; this will not run]"
    if bits == "x86-64":
        return f"  [{bits} static -- 64-bit targets only; i386 works on all four]"
    return f"  [{bits} static]"


def _find_bundle(ctx: "Ctx", want: str) -> str:
    """Resolve a bundle by name prefix, e.g. '01-core' -> slax/modules/01-core.sb."""
    mods = ctx.p("slax", "modules")
    hit = next((n for n in sorted(os.listdir(mods))
                if n.startswith(want) and n.endswith(".sb")), None)
    if hit is None:
        raise RuntimeError(f"no bundle matching {want!r} in slax/modules")
    return os.path.join(mods, hit)


@verb("initramfs.modules")
def v_initramfs_modules(ctx: Ctx, step: dict) -> None:
    """Add kernel modules to the initramfs -- NVMe, RAID, iSCSI, exotic NICs.

    The destination is lib/modules/<release>/, and <release> is READ FROM THE TREE.
    Hardcoding it breaks 32-bit, where the kernel carries LOCALVERSION=-smp and the
    directory is 6.1.38-smp rather than 6.1.38.

    No depmod is needed: modprobe_everything() finds modules by walking the tree with
    `find /lib/modules/ | fgrep .ko`, not by consulting modules.dep.
    """
    mods = step.get("modules") or []
    if not mods:
        raise RuntimeError("initramfs.modules: no modules listed")
    subdir = (step.get("subdir") or "kernel/extra").strip("/")
    from_bundle = step.get("from_bundle")
    if ctx.dry:
        src = f" from {from_bundle}" if from_bundle else ""
        for m in mods:
            ctx.say(f"would add {os.path.basename(m)}{src} to lib/modules/<release>/{subdir}/")
        return

    import tempfile
    work = tempfile.mkdtemp(prefix="kitchen-irfs-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    try:
        # The ISO already carries far more modules than the initramfs does -- 4,766 in
        # 01-core.sb against 301 in initrfs.img on 64-bit Debian, because
        # initramfs_create copies whole subtrees by directory and skips most of them.
        # Promoting one from a bundle needs no external file and is the usual answer to
        # "Slax boots on this machine but cannot find its own data".
        pool = None
        if from_bundle:
            pool = os.path.join(work, "pool")
            hit = _find_bundle(ctx, from_bundle)
            # Debian's bundles are merged-usr (lib -> usr/lib), Slackware's are not, so
            # the module tree is at usr/lib/modules on one flavour and lib/modules on
            # the other. Extracting only one silently yields an empty pool.
            r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", pool, hit,
                                "lib/modules", "usr/lib/modules"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"initramfs.modules: unsquashfs {os.path.basename(hit)}: "
                                   f"{r.stderr.strip()[:200]}")
            n = sum(len(f) for _, _, f in os.walk(pool))
            if n == 0:
                raise RuntimeError(
                    f"initramfs.modules: {os.path.basename(hit)} contains no module tree "
                    f"at lib/modules or usr/lib/modules")
            ctx.say(f"module pool: {os.path.basename(hit)} ({n:,} files)")

        tree = _initramfs_unpack(ctx, work)
        mdir = _initramfs_module_dir(tree)
        release = os.path.basename(mdir)
        target = os.path.join(mdir, subdir)
        os.makedirs(target, exist_ok=True)
        for m in mods:
            if pool:
                want = m if m.endswith(".ko") else m + ".ko"
                found = [os.path.join(dp, f) for dp, _, fs in os.walk(pool)
                         for f in fs if f == os.path.basename(want)]
                if not found:
                    raise RuntimeError(
                        f"initramfs.modules: {want} not found in {from_bundle}. "
                        f"Note many common storage drivers (dm-mod, md-mod, raid1, "
                        f"virtio_*) are compiled INTO the Slax kernel, not shipped as "
                        f"modules, so they need no promotion.")
                local = found[0]
            else:
                local = m if os.path.isabs(m) else os.path.join(ctx.recipe_dir, m)
            if not os.path.isfile(local):
                raise RuntimeError(f"initramfs.modules: no such module: {local}")
            name = os.path.basename(local)
            if not name.endswith(".ko"):
                # initramfs_create decompresses .ko.gz/.ko.xz at build time because the
                # shipped modprobe (busybox 1.26.2) cannot read compressed modules.
                raise RuntimeError(
                    f"initramfs.modules: {name} is not a plain .ko. Decompress it first "
                    f"-- the shipped busybox modprobe cannot load compressed modules.")
            shutil.copy2(local, os.path.join(target, name))
            ctx.say(f"initramfs: + lib/modules/{release}/{subdir}/{name}")
        ctx.say(f"module directory read from the tree: {release}")
        _initramfs_pack(ctx, tree)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@verb("initramfs.patch")
def v_initramfs_patch(ctx: Ctx, step: dict) -> None:
    """Patch the boot scripts inside the initramfs -- init, livekitlib, shutdown, config.

    This is the most dangerous verb in the toolkit. These four files ARE the boot: a
    stray character in livekitlib and the machine stops somewhere in early init with no
    message, because the thing that would print one is the file you just broke.

    So it is deliberately strict rather than general. No unified diffs, no line numbers,
    no regex by default -- an exact string that must appear an exact number of times.
    A patch written against 12.2.0 that no longer matches FAILS rather than silently
    doing nothing, which is the failure mode that makes "it stopped working three
    releases ago and nobody noticed" possible.

    Every patched file is then checked with `sh -n`. All four are shell scripts and all
    four parse clean as shipped, so a syntax error is caught here rather than at boot.
    """
    edits = step.get("edits") or []
    if not edits:
        raise RuntimeError("initramfs.patch: no edits listed")
    if ctx.dry:
        for e in edits:
            ctx.say(f"would patch {e['file']}: {_short(e.get('find', ''))!r}")
        return

    import tempfile
    work = tempfile.mkdtemp(prefix="kitchen-irfs-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    try:
        tree = _initramfs_unpack(ctx, work)
        touched: set = set()
        for e in edits:
            rel = e["file"].lstrip("/")
            path = _under(tree, rel, "initramfs.patch", "file")
            if not os.path.isfile(path):
                raise RuntimeError(f"initramfs.patch: {rel} not in the initramfs")

            # Pin the patch to a known file, so a recipe cannot quietly apply to a
            # version it was never written for.
            want_sha = e.get("expect_sha256")
            if want_sha:
                got = hashlib.sha256(open(path, "rb").read()).hexdigest()
                if got != want_sha:
                    raise RuntimeError(
                        f"initramfs.patch: {rel} is sha256 {got[:16]}..., recipe expects "
                        f"{want_sha[:16]}.... Refusing -- this patch was written for a "
                        f"different build.")

            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            find, repl = e["find"], e.get("replace", "")
            want_n = int(e.get("count", 1))
            got_n = text.count(find)
            if got_n != want_n:
                raise RuntimeError(
                    f"initramfs.patch: {rel}: expected {want_n} occurrence(s) of "
                    f"{_short(find)!r}, found {got_n}. Refusing rather than guessing.")
            text = text.replace(find, repl)
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write(text)
            touched.add(rel)
            ctx.say(f"initramfs: patched {rel}  {_short(find)!r} -> {_short(repl)!r}")

        # Syntax-check anything that looks like a shell script. Cheap, and it is the
        # difference between "refuses to ship" and "bricks the boot".
        for rel in sorted(touched):
            path = os.path.join(tree, rel)
            with open(path, "rb") as f:
                shebang = f.readline()
            if not shebang.startswith(b"#!") or (b"sh" not in shebang):
                continue
            shell = "bash" if b"bash" in shebang and shutil.which("bash") else "sh"
            r = subprocess.run([shell, "-n", path], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(
                    f"initramfs.patch: {rel} no longer parses as {shell} after patching:\n"
                    f"  {r.stderr.strip()[:300]}\n"
                    f"Refusing to build an initramfs that cannot boot.")
            ctx.say(f"{shell} -n {rel}: ok")
        _initramfs_pack(ctx, tree)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _short(t: str, n: int = 48) -> str:
    t = " ".join(t.split())
    return t if len(t) <= n else t[:n - 1] + "\u2026"


@verb("initramfs.busybox")
def v_initramfs_busybox(ctx: Ctx, step: dict) -> None:
    """Replace the initramfs busybox and regenerate its applet symlinks.

    This is a behaviour-compatibility change, not a file drop, which is why it is a verb
    rather than an `initramfs.files` entry. Swapping the binary alone leaves 245 symlinks
    pointing at an applet set that no longer matches it.

    THREE THINGS THE REGENERATION HAS TO GET RIGHT, all of them load-bearing:

      * `blkid` and `eject` are REAL FILES that shadow busybox applets of the same name,
        and livekitlib parses `blkid -o full`, an option busybox's applet does not have.
        A symlink must never overwrite an existing file, which is what upstream's
        `[ ! -e ]` guard does and what is reproduced here.

      * `bin/init` must not exist as a busybox symlink, or the `init` applet shadows the
        `/init` script and the machine boots into the wrong thing. Upstream deletes it
        after generating; so do we. 1.38.0 adds `nuke` and `linuxrc`, which deserve the
        same treatment if you move to it.

      * STALE SYMLINKS MUST GO. The shipped 1.26.2 has a `catv` applet; 1.37.0 removed
        it. Leaving the symlink behind gives a `catv` in PATH that fails at runtime
        rather than being absent. Upstream never had to think about this because it
        builds the tree from empty; we are editing one in place.

    Upstream derives the applet list by scraping busybox's human-readable usage text
    (`busybox | grep , | grep -v Copyright | tr "," " "`), which is not a stable
    interface -- see known-upstream-bugs issue 10. This uses `busybox --list`, which is
    documented and which the shipped 1.26.2 already answers correctly.
    """
    src = step.get("src")
    if not src:
        raise RuntimeError("initramfs.busybox: need `src` (build one with "
                           "tools/build-busybox.sh)")
    local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
    if not os.path.isfile(local):
        raise RuntimeError(
            f"initramfs.busybox: {local} not found.\n"
            f"  Build it first:  tools/build-busybox.sh --check-parity")

    # The initramfs has no dynamic loader, so a non-static binary would produce a
    # machine that gets as far as /init and then cannot run a single command.
    kind = ""
    if shutil.which("file"):
        kind = subprocess.run(["file", "-b", local], capture_output=True,
                              text=True).stdout.strip()
        if "statically linked" not in kind:
            raise RuntimeError(f"initramfs.busybox: {src} is not static -- {kind}")
        if "Intel 80386" not in kind:
            ctx.say(f"warning: {src} is not i386 ({kind.split(',')[0]}); it will work "
                    f"only on the 64-bit targets")

    if ctx.dry:
        ctx.say(f"would install {src} as bin/busybox and regenerate applet symlinks")
        return

    import tempfile
    work = tempfile.mkdtemp(prefix="kitchen-bb-",
                            dir=os.path.dirname(os.path.abspath(ctx.work)))
    try:
        tree = _initramfs_unpack(ctx, work)
        bindir = os.path.join(tree, "bin")
        target = os.path.join(bindir, "busybox")
        old_size = os.path.getsize(target) if os.path.isfile(target) else 0

        # Read the OLD applet set before overwriting, so stale links can be identified.
        old_applets = _busybox_applets(target) if old_size else set()

        shutil.copy2(local, target)
        os.chmod(target, 0o755)
        new_applets = _busybox_applets(target)
        ctx.say(f"busybox {old_size:,} -> {os.path.getsize(target):,} bytes, "
                f"{len(old_applets)} -> {len(new_applets)} applets")

        gone = sorted(old_applets - new_applets)
        if gone:
            ctx.say(f"applets removed upstream: {', '.join(gone)}")

        added = removed = kept = 0
        for name in sorted(new_applets):
            if name == "init":          # never shadow the /init script
                continue
            link = os.path.join(bindir, name)
            if os.path.lexists(link):
                # A real file here is a deliberate shadow (blkid, eject) -- leave it.
                if not os.path.islink(link):
                    kept += 1
                continue
            os.symlink("busybox", link)
            added += 1

        # Stale links: point at busybox but name an applet this build does not have.
        for name in gone:
            link = os.path.join(bindir, name)
            if os.path.islink(link) and os.path.basename(os.readlink(link)) == "busybox":
                os.unlink(link)
                removed += 1

        # Upstream deletes bin/init after generating; reproduce that unconditionally.
        init_link = os.path.join(bindir, "init")
        if os.path.islink(init_link):
            os.unlink(init_link)

        ctx.say(f"symlinks: +{added} new, -{removed} stale, {kept} real files left alone")
        for shadow in ("blkid", "eject"):
            p = os.path.join(bindir, shadow)
            if os.path.isfile(p) and not os.path.islink(p):
                ctx.say(f"  {shadow}: still a real binary, as it must be")
            else:
                raise RuntimeError(
                    f"initramfs.busybox: bin/{shadow} is no longer a real file. "
                    f"livekitlib parses `blkid -o full`, which busybox cannot do.")

        _initramfs_pack(ctx, tree)
        ctx.changes.append(f"initramfs busybox -> {os.path.basename(local)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _busybox_applets(path: str) -> set:
    """Applet names from `busybox --list`.

    Documented interface, unlike upstream's usage-text scraping (issue 10). Needs to
    EXECUTE an i386 binary, so a build host without IA32 support cannot do this -- which
    is the same constraint the target kernel has, and worth failing clearly on.
    """
    r = subprocess.run([path, "--list"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(
            f"initramfs.busybox: `{os.path.basename(path)} --list` failed. The binary is "
            f"i386; this host may lack IA32 support (the same thing a kernel without "
            f"CONFIG_IA32_EMULATION lacks). stderr: {r.stderr.strip()[:200]}")
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


@verb("rootcopy.files")
def v_rootcopy_files(ctx: Ctx, step: dict) -> None:
    """Drop files into /slax/rootcopy/, which livekit copies onto the union at boot.

    No bundle rebuild, no squashfs work -- the cheapest customization there is.
    Upstream ships no rootcopy directory at all, so this creates it.
    """
    for spec in step["files"]:
        dest = _under(ctx.p("slax", "rootcopy"), spec["dest"], "rootcopy.files")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if ctx.dry:
            ctx.say(f"would place rootcopy/{spec['dest']}")
            continue
        if "content" in spec:
            with open(dest, "w") as f:
                f.write(spec["content"])
        else:
            src = spec["src"]
            local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
            shutil.copy2(local, dest)
        if "mode" in spec:
            os.chmod(dest, int(spec["mode"], 8))
        ctx.say(f"rootcopy: {spec['dest']}")


@verb("rootcopy.preinit")
def v_rootcopy_preinit(ctx: Ctx, step: dict) -> None:
    """Install /slax/rootcopy/run/preinit.sh -- livekit's official pre-boot hook.

    livekitlib's user_preinit() SOURCES this file (it does `. "$SRC" "$2"`) just before
    change_root, with the assembled union directory as $1. So it runs after the bundles
    are mounted and the union exists, but before the real init starts -- the last point
    at which you can touch the filesystem the system is about to boot into.

    Being sourced rather than executed has two consequences worth knowing: a shebang is
    decorative, and anything the script does to the initramfs shell's environment (cd,
    exported vars, `exit`) affects init itself. Prefer a subshell for anything risky.
    """
    script = step.get("script")
    src = step.get("src")
    if not script and not src:
        raise RuntimeError("rootcopy.preinit: need `script` or `src`")
    dest = ctx.p("slax", "rootcopy", "run", "preinit.sh")
    if ctx.dry:
        ctx.say("would install slax/rootcopy/run/preinit.sh")
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if script:
        with open(dest, "w") as f:
            if not script.startswith("#!"):
                f.write("#!/bin/sh\n")
            f.write(script if script.endswith("\n") else script + "\n")
    else:
        local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
        shutil.copy2(local, dest)
    os.chmod(dest, 0o755)
    ctx.say(f"slax/rootcopy/run/preinit.sh ({os.path.getsize(dest)} bytes) "
            "-- sourced by livekit just before change_root")


# ----------------------------------------------------------- bundles --------
#
# Every bundle on every shipped image has superblock flags 0x04e0, because upstream uses
# one mksquashfs line in four places: livekitlib's create_bundle, dir2sb, savechanges and
# both flavours' module builders. Matching it exactly is what keeps a built bundle
# indistinguishable from a shipped one, so `kitchen probe` can still reason about an ISO.

MKSQUASHFS_ARGS = ["-comp", "xz", "-b", "1024K", "-Xbcj", "x86",
                   "-always-use-fragments", "-noappend"]


def _under(root: str, rel: str, verb: str, what: str = "dest") -> str:
    """Resolve `rel` inside `root`, refusing anything that escapes it.

    Every verb that writes a caller-named path goes through here. Without it a
    `dest: ../../../etc/cron.d/x` walks straight out of the work tree and writes to the
    host -- and it would do so from a recipe declaring `privilege: none`, which is
    exactly the set of verbs a reader trusts to be harmless. Recipes are meant to be
    shared, so "the author could have written anything" is not an answer here.

    Symlinks are resolved too: a bundle that ships `etc -> /etc` would otherwise turn a
    later innocuous-looking `dest: etc/passwd` into a host write.
    """
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel.lstrip("/")))
    if full != base and not full.startswith(base + os.sep):
        raise RuntimeError(
            f"{verb}: {what} {rel!r} resolves outside the tree it belongs to "
            f"({full}). Paths are relative to the root of that tree; '..' is refused.")
    return full


def _bundle_name(raw: str, verb: str) -> str:
    """Validate and normalise a bundle filename.

    Load order is the numeric prefix and higher wins, so a bundle without one has no
    defined position in the stack. Refusing here beats shipping something that silently
    never overrides anything.
    """
    name = raw if raw.endswith(".sb") else raw + ".sb"
    if not re.match(r"^\d\d-", name):
        raise RuntimeError(
            f"{verb}: bundle name must start with NN- (load order is the numeric "
            f"prefix, and higher wins); got {name!r}")
    return name


def _make_bundle(ctx: "Ctx", src_dir: str, name: str, verb: str) -> str:
    """mksquashfs src_dir into slax/modules/<name> with upstream's exact parameters."""
    if not os.listdir(src_dir):
        raise RuntimeError(f"{verb}: {src_dir} is empty; refusing to build an empty bundle")
    mods = ctx.p("slax", "modules")
    os.makedirs(mods, exist_ok=True)
    target = os.path.join(mods, name)
    if os.path.exists(target):
        raise RuntimeError(f"{verb}: slax/modules/{name} already exists. Pick another "
                           f"number, or remove it first with bundle.remove.")
    r = subprocess.run(["mksquashfs", src_dir, target] + MKSQUASHFS_ARGS,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{verb}: mksquashfs failed: {r.stderr.strip()[:300]}")
    n = sum(len(f) for _, _, f in os.walk(src_dir))
    ctx.say(f"built slax/modules/{name} ({os.path.getsize(target) // 1024} KiB, {n} files)")
    ctx.changes.append(f"slax/modules/{name}")
    return target


def _place_files(ctx: "Ctx", root: str, files: list, verb: str) -> None:
    """Write a list of {dest, src|content, mode} specs under root."""
    for spec in files:
        dest = _under(root, spec["dest"], verb)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if "content" in spec:
            with open(dest, "w") as f:
                f.write(spec["content"])
        else:
            src = spec["src"]
            local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
            if os.path.isdir(local):
                shutil.copytree(local, dest, dirs_exist_ok=True, symlinks=True)
            elif os.path.isfile(local):
                shutil.copy2(local, dest)
            else:
                raise RuntimeError(f"{verb}: source not found: {local}")
        if "mode" in spec:
            os.chmod(dest, int(str(spec["mode"]), 8))
        ctx.say(f"  {spec['dest']}")


@verb("bundle.fromDir")
def v_bundle_fromdir(ctx: Ctx, step: dict) -> None:
    """Pack a directory into a bundle, as if it were a filesystem root.

    The directory's contents become the root of the bundle, so ./usr/bin/foo lands at
    /usr/bin/foo in the union. This is the offline equivalent of upstream's dir2sb.
    """
    name = _bundle_name(step["bundle"], "bundle.fromDir")
    src = step["src"]
    local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
    if ctx.dry:
        ctx.say(f"would pack {src} -> slax/modules/{name}")
        return
    if not os.path.isdir(local):
        raise RuntimeError(f"bundle.fromDir: not a directory: {local}")
    _make_bundle(ctx, local, name, "bundle.fromDir")


@verb("bundle.files")
def v_bundle_files(ctx: Ctx, step: dict) -> None:
    """Build a bundle from a list of files given inline or by path.

    The same shape as rootcopy.files, but the result is a real bundle rather than a
    rootcopy drop. Worth the difference when you want the files to be one movable file,
    to be skippable with noload=, or to sit at a defined point in the stack -- rootcopy
    always lands in the writable layer and cannot be turned off at the boot prompt.
    """
    files = step.get("files") or []
    if not files:
        raise RuntimeError("bundle.files: no files listed")
    name = _bundle_name(step["bundle"], "bundle.files")
    if ctx.dry:
        for spec in files:
            ctx.say(f"would add {spec['dest']} to {name}")
        return
    import tempfile
    work = tempfile.mkdtemp(prefix="kitchen-bf-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    try:
        root = os.path.join(work, "root")
        os.makedirs(root)
        _place_files(ctx, root, files, "bundle.files")
        _make_bundle(ctx, root, name, "bundle.files")
    finally:
        shutil.rmtree(work, ignore_errors=True)


@verb("bundle.fromTarball")
def v_bundle_fromtarball(ctx: Ctx, step: dict) -> None:
    """Unpack a tarball and pack it as a bundle.

    `strip: 1` drops a leading directory, the way tar --strip-components does, because
    most published tarballs are wrapped in a versioned top-level folder that you almost
    never want at the root of the union. `prefix:` puts the contents somewhere other than
    the root, e.g. prefix: /opt for a self-contained application tree.
    """
    name = _bundle_name(step["bundle"], "bundle.fromTarball")
    src = step["src"]
    want = step.get("sha256")
    strip = int(step.get("strip", 0))
    prefix = (step.get("prefix") or "").strip("/")
    if ctx.dry:
        ctx.say(f"would unpack {src} -> slax/modules/{name}"
                + (f" under /{prefix}" if prefix else ""))
        return

    import tarfile
    import tempfile
    work = tempfile.mkdtemp(prefix="kitchen-bt-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    try:
        if re.match(r"^https?://", src):
            archive = os.path.join(work, "src.tar")
            with urllib.request.urlopen(src, timeout=120) as r, open(archive, "wb") as f:
                shutil.copyfileobj(r, f)
        else:
            archive = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
            if not os.path.isfile(archive):
                raise RuntimeError(f"bundle.fromTarball: no such file: {archive}")
        got = sha256(archive)
        if want and got != want:
            raise RuntimeError(f"bundle.fromTarball: sha256 mismatch\n  want {want}\n  got  {got}")
        if not want:
            ctx.say(f"warning: no sha256 pinned for {os.path.basename(src)} (got {got[:16]}...)")

        root = os.path.join(work, "root")
        dest = os.path.join(root, prefix) if prefix else root
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(archive) as t:
            members = []
            for m in t.getmembers():
                # Refuse absolute paths and .. escapes rather than trusting the archive.
                if m.name.startswith("/") or ".." in m.name.split("/"):
                    raise RuntimeError(f"bundle.fromTarball: unsafe path in archive: {m.name}")
                if strip:
                    parts = m.name.split("/")
                    if len(parts) <= strip:
                        continue
                    m.name = "/".join(parts[strip:])
                members.append(m)
            if not members:
                raise RuntimeError(f"bundle.fromTarball: nothing left after strip: {strip}")
            t.extractall(dest, members=members)
        ctx.say(f"unpacked {len(members)} entries from {os.path.basename(src)}"
                + (f" under /{prefix}" if prefix else ""))
        _make_bundle(ctx, root, name, "bundle.fromTarball")
    finally:
        shutil.rmtree(work, ignore_errors=True)


@verb("bundle.renumber")
def v_bundle_renumber(ctx: Ctx, step: dict) -> None:
    """Change a bundle's numeric prefix, which is its position in the stack.

    Load order is the prefix and higher wins, so this is how you make one bundle override
    another without rebuilding either. Renaming 05-chromium.sb to 95-chromium.sb moves it
    above everything; renaming it to 00- puts it below the core.
    """
    mods = ctx.p("slax", "modules")
    if not os.path.isdir(mods):
        raise RuntimeError("bundle.renumber: no slax/modules in the work tree")
    pat = re.compile(step["match"])
    to = str(step["to"]).zfill(2)
    if not re.match(r"^\d\d$", to):
        raise RuntimeError(f"bundle.renumber: 'to' must be two digits; got {step['to']!r}")
    hits = sorted(n for n in os.listdir(mods) if pat.search(n) and n.endswith(".sb"))
    if not hits:
        ctx.say(f"no bundle matched /{step['match']}/ (nothing renumbered)")
        return
    for n in hits:
        new = re.sub(r"^\d\d-", to + "-", n)
        if new == n:
            ctx.say(f"{n} already at {to}-")
            continue
        if os.path.exists(os.path.join(mods, new)):
            raise RuntimeError(f"bundle.renumber: {new} already exists")
        if not ctx.dry:
            os.rename(os.path.join(mods, n), os.path.join(mods, new))
        ctx.say(f"renumbered {n} -> {new}")
        ctx.changes.append(f"slax/modules/{new}")


@verb("bundle.script")
def v_bundle_script(ctx: Ctx, step: dict) -> None:
    """Run an arbitrary script inside a chroot of stacked bundles, and package the delta.

    bundle.packages generalised: instead of calling apt or installpkg, it runs whatever
    you give it, snapshots the tree before and after, and packs what changed into a new
    bundle. Use it for the things a package manager cannot do -- compile something,
    generate host keys, run a vendor installer, seed a configuration.

    The same two caveats apply as for bundle.packages, and both are load-bearing:

      * the delta is added OR MODIFIED files, never names alone. A filename-only diff
        misses var/lib/dpkg/status, and a bundle without it leaves new binaries invisible
        to the package database.
      * BUNDLE_EXCLUDE strips the runtime directories the chroot needed but a bundle must
        not ship, plus caches and lockfiles.

    There is no network by default: pass network: true to say you meant it, so a recipe
    that quietly depends on the internet is visible in the YAML rather than at run time.
    """
    script = step.get("script")
    if not script:
        raise RuntimeError("bundle.script: no script given")
    name = _bundle_name(step["bundle"], "bundle.script")
    stack = step.get("from") or ["01-core"]
    if ctx.dry:
        ctx.say(f"would run a script in {'+'.join(stack)} -> slax/modules/{name}")
        return
    for tool in ("unsquashfs", "mksquashfs", "chroot"):
        if not shutil.which(tool):
            raise RuntimeError(f"bundle.script: {tool} not installed")

    import tempfile
    build = tempfile.mkdtemp(prefix="kitchen-bs-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    root = os.path.join(build, "root")
    try:
        os.makedirs(root, exist_ok=True)
        picked = []
        for want in stack:
            hit = _find_bundle(ctx, want)
            picked.append(os.path.basename(hit))
            r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", root, hit],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"unsquashfs {os.path.basename(hit)}: "
                                   f"{r.stderr.strip()[:300]}")
        ctx.say(f"unpacked {' + '.join(picked)} as the build root")
        _prepare_chroot(root)

        before = _manifest(root)
        sp = os.path.join(root, "tmp", "kitchen-script")
        with open(sp, "w") as f:
            f.write(script)
        os.chmod(sp, 0o755)
        shell = step.get("shell") or "/bin/sh"
        r = _in_chroot(root, [shell, "/tmp/kitchen-script"])
        if r.returncode != 0:
            raise RuntimeError(f"bundle.script: script failed (exit {r.returncode}):\n"
                               + (r.stderr.strip() or r.stdout.strip())[-1500:])
        if r.stdout.strip():
            for ln in r.stdout.strip().splitlines()[-8:]:
                ctx.say(f"  | {ln[:110]}")
        os.unlink(sp)
        after = _manifest(root)

        added = [k for k in after if k not in before]
        modified = [k for k in after if k in before and after[k] != before[k]]
        keep = sorted(k for k in added + modified if not BUNDLE_EXCLUDE.search(k))
        ctx.say(f"delta: {len(added)} added, {len(modified)} modified, "
                f"{len(keep)} kept after exclusions")
        if not keep:
            raise RuntimeError("bundle.script: the script changed nothing that survives "
                               "the exclusion list; nothing to package")

        stage = os.path.join(build, "stage")
        for rel in keep:
            src, dst = os.path.join(root, rel), os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.islink(src):
                if not os.path.lexists(dst):
                    os.symlink(os.readlink(src), dst)
            elif os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                shutil.copystat(src, dst)
            else:
                shutil.copy2(src, dst)
                st = os.lstat(src)
                os.chown(dst, st.st_uid, st.st_gid)
        _make_bundle(ctx, stage, name, "bundle.script")
    finally:
        shutil.rmtree(build, ignore_errors=True)


@verb("bundle.remove")
def v_bundle_remove(ctx: Ctx, step: dict) -> None:
    pat = re.compile(step["match"])
    mods = ctx.p("slax", "modules")
    if not os.path.isdir(mods):
        raise RuntimeError("bundle.remove: no slax/modules in the work tree")
    hits = sorted(n for n in os.listdir(mods) if pat.search(n))
    if not hits:
        ctx.say(f"no bundle matched /{step['match']}/ (nothing removed)")
        return
    for n in hits:
        size = os.path.getsize(os.path.join(mods, n))
        if not ctx.dry:
            os.unlink(os.path.join(mods, n))
        ctx.say(f"removed {n} (-{size // 1048576} MiB)")


# ---- syslinux config editing -------------------------------------------------
# isolinux.cfg (CD) and syslinux.cfg (USB/HDD) have DIFFERENT menus upstream: the CD
# one has no perchdir= entries at all because optical media cannot persist. Recipes
# therefore say which targets they mean, and default to both.

def _cfg_paths(ctx: Ctx, targets) -> list[str]:
    return [ctx.p("slax", "boot", t) for t in (targets or ["isolinux.cfg", "syslinux.cfg"])]


def _render_entry(e: dict) -> str:
    out = [f"LABEL {e['label']}", f"MENU LABEL {e['menu_label']}"]
    for key in ("kernel", "linux", "com32", "initrd", "append"):
        if key in e:
            out.append(f"{key.upper()} {e[key]}")
    return "\n".join(out) + "\n"


@verb("boot.menu")
def v_boot_menu(ctx: Ctx, step: dict) -> None:
    for path in _cfg_paths(ctx, step.get("targets")):
        if not os.path.isfile(path):
            ctx.say(f"skip {os.path.basename(path)} (not present)")
            continue
        text = open(path).read()
        name = os.path.basename(path)

        if "remove" in step:
            lbl = step["remove"]
            new = re.sub(rf"(?ms)^LABEL\s+{re.escape(lbl)}\s*$.*?(?=^LABEL\s|\Z)", "", text)
            if new != text:
                text = new
                ctx.say(f"{name}: removed LABEL {lbl}")
        if "add" in step:
            e = step["add"]
            if re.search(rf"(?m)^LABEL\s+{re.escape(e['label'])}\s*$", text):
                ctx.say(f"{name}: LABEL {e['label']} already present, skipping")
            else:
                block = _render_entry(e)
                if e.get("position") == "top":
                    m = re.search(r"(?m)^LABEL\s", text)
                    text = text[:m.start()] + block + "\n" + text[m.start():] if m else text + "\n" + block
                else:
                    text = text.rstrip("\n") + "\n\n" + block
                ctx.say(f"{name}: added LABEL {e['label']} ({e['menu_label']})")
        if "timeout" in step:
            text, n = re.subn(r"(?m)^TIMEOUT\s+\d+\s*$", f"TIMEOUT {step['timeout']}", text)
            ctx.say(f"{name}: TIMEOUT {step['timeout']}" if n else f"{name}: no TIMEOUT to set")
        if "default" in step:
            text, n = re.subn(r"(?m)^(MENU\s+HIDDENKEY\s+Enter\s+)\S+\s*$",
                              rf"\g<1>{step['default']}", text)
            ctx.say(f"{name}: default -> {step['default']}" if n else f"{name}: no default key found")

        if not ctx.dry:
            open(path, "w").write(text)


@verb("boot.cmdline")
def v_boot_cmdline(ctx: Ctx, step: dict) -> None:
    """Add or remove kernel parameters on APPEND lines.

    The schema already requires `append`/`remove` to be arrays, so _listify is only
    defence for callers that bypass validation -- without it `for a in "toram"` would
    append the letters t, o, r, a, m as five separate parameters.
    """
    def _listify(v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else list(v)

    add = _listify(step.get("append"))
    drop = _listify(step.get("remove"))
    # A step that changes nothing is a mistake, and the commonest way to write one is a
    # misspelled field -- the verb reference said `add:` for a while, which this verb
    # silently ignored while reporting "cmdline updated on 2 entries". The step schema
    # does not constrain per-verb fields, so this is the only place to catch it.
    if not add and not drop:
        raise RuntimeError(
            "boot.cmdline: nothing to do -- give `append:` and/or `remove:` "
            f"(got fields: {', '.join(sorted(k for k in step if k != 'verb'))})")
    only = set(step.get("labels", []))
    for path in _cfg_paths(ctx, step.get("targets")):
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        lines = open(path).read().split("\n")
        cur = None
        touched = 0
        for i, ln in enumerate(lines):
            m = re.match(r"^LABEL\s+(\S+)", ln)
            if m:
                cur = m.group(1)
            if not ln.startswith("APPEND "):
                continue
            if only and cur not in only:
                continue
            parts = ln[len("APPEND "):].split()
            for d in drop:
                parts = [p for p in parts if p != d and not p.startswith(d + "=")]
            for a in add:
                key = a.split("=", 1)[0]
                parts = [p for p in parts if p != key and not p.startswith(key + "=")]
                parts.append(a)
            new_line = "APPEND " + " ".join(parts)
            # Count what actually CHANGED, not what was visited. Reporting a number that
            # is always the entry count is a report that cannot be wrong, which is worse
            # than no report at all.
            if new_line != ln:
                lines[i] = new_line
                touched += 1
        if not ctx.dry:
            open(path, "w").write("\n".join(lines))
        ctx.say(f"{name}: cmdline updated on {touched} entr{'y' if touched == 1 else 'ies'}")


@verb("boot.isohybrid")
def v_boot_isohybrid(ctx: Ctx, step: dict) -> None:
    """Make the ISO dd-able to a USB stick.

    Stock Slax ISOs have all-zero bytes 0..512 -- no MBR, no partition table, no GPT --
    so `dd if=slax.iso of=/dev/sdX` produces a stick that will not boot on anything.
    The actual work happens at mastering time, so this just records the intent.
    """
    hdr = "/usr/lib/ISOLINUX/isohdpfx.bin"
    if not os.path.isfile(hdr):
        raise RuntimeError(f"boot.isohybrid: {hdr} missing (apt-get install isolinux)")
    ctx.hint("hybrid", True)


GRUB_MODULES = ("part_gpt part_msdos fat iso9660 normal linux configfile "
                "search search_fs_file search_fs_uuid search_label "
                "all_video echo test ls reboot halt chain")


def _parse_syslinux(path: str) -> list[dict]:
    """Read LABEL blocks out of a syslinux/isolinux config.

    boot.uefi mirrors these into the GRUB menu rather than hardcoding entries, so any
    entry another recipe added (serial-console, memtest86plus) shows up under UEFI too,
    provided boot.uefi runs last.
    """
    entries, cur = [], None
    for ln in open(path):
        ln = ln.rstrip("\n")
        m = re.match(r"^LABEL\s+(\S+)", ln)
        if m:
            cur = {"label": m.group(1)}
            entries.append(cur)
            continue
        if cur is None:
            continue
        for key, pat in (("menu_label", r"^\s*MENU LABEL\s+(.*)$"),
                         ("kernel", r"^\s*(?:KERNEL|LINUX)\s+(.*)$"),
                         ("append", r"^\s*APPEND\s+(.*)$")):
            mm = re.match(pat, ln, re.I)
            if mm:
                cur[key] = mm.group(1).strip()
        if re.match(r"^\s*MENU DISABLED", ln, re.I):
            cur["disabled"] = True
    return [e for e in entries
            if e.get("kernel") and not e.get("disabled")]


def _grub_cfg(entries: list[dict]) -> str:
    out = ["# Generated by slax-kitchen boot.uefi -- do not hand-edit.",
           "# Mirrors the syslinux menu so BIOS and UEFI offer the same choices.",
           "set timeout=5", "set default=0",
           "insmod all_video", "insmod iso9660", "", ]
    for e in entries:
        title = e.get("menu_label", e["label"]).replace("'", "'\\''")
        append = e.get("append", "")
        # syslinux takes initrd= as a kernel parameter; GRUB needs a separate command.
        initrd = ""
        m = re.search(r"initrd=(\S+)", append)
        if m:
            initrd = m.group(1)
            append = append.replace(m.group(0), "").strip()
        append = re.sub(r"\s+", " ", append)
        out.append(f"menuentry '{title}' {{")
        out.append(f"    linux {e['kernel']} {append}")
        if initrd:
            out.append(f"    initrd {initrd}")
        out.append("}")
        out.append("")
    out += ["menuentry 'Reboot' { reboot }", "menuentry 'Power off' { halt }", ""]
    return "\n".join(out)


@verb("iso.metadata")
def v_iso_metadata(ctx: Ctx, step: dict) -> None:
    """Set the ISO9660 volume descriptor fields.

    Upstream leaves most of them empty: publisher, data preparer, volume set, copyright,
    abstract and bibliography are all blank on every shipped image, and only volume id,
    system id and application id carry anything. Filling them is the difference between
    a rebuilt image that identifies itself and one that silently claims to be stock.

    Mastering-time settings, so they are recorded as pack hints rather than written into
    the tree -- there is nowhere in the tree they could live.
    """
    # (field width in the PVD, what the field is called in ISO9660)
    fields = {
        "volid": (32, "Volume Identifier"),
        "appid": (128, "Application Identifier"),
        "sysid": (32, "System Identifier"),
        "publisher": (128, "Publisher Identifier"),
        "preparer": (128, "Data Preparer Identifier"),
    }
    given = {k: v for k, v in step.items() if k in fields}
    if not given:
        raise RuntimeError("iso.metadata: nothing to set -- give volid, appid, sysid, "
                           "publisher or preparer")
    for k, v in given.items():
        limit, what = fields[k]
        v = str(v)
        # ISO9660 pads these to a fixed width; anything longer is truncated by the
        # mastering tool without a word, so say so here instead.
        if len(v) > limit:
            raise RuntimeError(
                f"iso.metadata: {k} is {len(v)} characters; the ISO9660 {what} field "
                f"holds {limit}. Shorten it -- the mastering tool would truncate it "
                f"silently.")
        if k == "volid" and re.search(r"[^A-Za-z0-9_.-]", v):
            ctx.say(f"warning: volid {v!r} contains characters outside A-Z 0-9 _ . - ; "
                    f"some systems show it differently")
        ctx.hint(k, v)


@verb("iso.checksums")
def v_iso_checksums(ctx: Ctx, step: dict) -> None:
    """Write a checksum file beside the finished ISO.

    Necessarily a pack hint: the checksum of an image cannot live inside that image.
    `kitchen pack` writes it after mastering, next to the output.
    """
    algo = (step.get("algorithm") or "sha256").lower()
    if algo not in ("sha256", "sha512"):
        raise RuntimeError(f"iso.checksums: unsupported algorithm {algo!r} "
                           f"(want sha256 or sha512)")
    ctx.hint("checksums", algo)
    if step.get("sign"):
        # Only record the request. Signing needs a key and a passphrase, neither of
        # which belongs in a recipe or in this process.
        ctx.hint("checksums_sign", str(step["sign"]))
        ctx.say("note: signing is requested but not performed by apply; "
                "`kitchen pack` will sign if gpg has the key")


@verb("boot.branding")
def v_boot_branding(ctx: Ctx, step: dict) -> None:
    """Change what the boot menu looks like: splash, help text, timeout, default entry.

    All four targets ship byte-identical isolinux.cfg and syslinux.cfg, so one edit here
    applies to every image at once.
    """
    targets = _cfg_paths(ctx, step.get("targets"))
    changed: list[str] = []

    for key, dest in (("bootlogo", "bootlogo.png"), ("helpbg", "zblack.png")):
        spec = step.get(key)
        if not spec:
            continue
        src = spec if isinstance(spec, str) else spec["src"]
        local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
        if not os.path.isfile(local):
            raise RuntimeError(f"boot.branding: {key} source not found: {local}")
        if not ctx.dry:
            shutil.copy2(local, ctx.p("slax", "boot", dest))
        ctx.say(f"replaced slax/boot/{dest} ({os.path.getsize(local)} bytes)")
        changed.append(dest)

    if "help" in step:
        text = step["help"]
        if isinstance(text, dict):
            src = text["src"]
            local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
            text = open(local).read()
        if not ctx.dry:
            with open(ctx.p("slax", "boot", "help.txt"), "w") as f:
                f.write(text)
        ctx.say(f"rewrote slax/boot/help.txt ({len(text)} bytes, "
                f"{len(text.splitlines())} lines)")

    # TIMEOUT is in TENTHS of a second -- the stock 40 is four seconds, not forty. Taking
    # it in seconds here and converting is the difference between "wait 10s" and a
    # one-second menu nobody can read.
    for path in targets:
        if not os.path.isfile(path):
            continue
        text = open(path).read()
        orig = text
        if "timeout" in step:
            secs = float(step["timeout"])
            tenths = int(round(secs * 10))
            if tenths < 0:
                raise RuntimeError("boot.branding: timeout cannot be negative")
            text = re.sub(r"(?mi)^TIMEOUT\s+\d+", f"TIMEOUT {tenths}", text)
            if not re.search(r"(?mi)^TIMEOUT\s+\d+", orig):
                text = "TIMEOUT %d\n" % tenths + text
        if "default" in step:
            want = str(step["default"])
            labels = [e["label"] for e in _parse_syslinux(path)]
            if want not in labels:
                raise RuntimeError(
                    f"boot.branding: no LABEL {want!r} in {os.path.basename(path)}; "
                    f"have {', '.join(labels) or '(none)'}. Add it with boot.menu first.")
            if re.search(r"(?mi)^MENU DEFAULT\s*$", text):
                text = re.sub(r"(?mi)^MENU DEFAULT\s*\n", "", text)
            text = re.sub(r"(?mi)^(LABEL\s+%s\s*\n)" % re.escape(want),
                          r"\1  MENU DEFAULT\n", text, count=1)
        if text != orig:
            if not ctx.dry:
                with open(path, "w") as f:
                    f.write(text)
            rel = os.path.relpath(path, ctx.tree)
            ctx.say(f"updated {rel}")
            changed.append(rel)

    if not changed and not ctx.dry:
        raise RuntimeError("boot.branding: nothing to do -- give bootlogo, helpbg, help, "
                           "timeout or default")


@verb("boot.grub")
def v_boot_grub(ctx: Ctx, step: dict) -> None:
    """Emit a GRUB snippet for chainloading this Slax from an EXISTING host bootloader.

    Distinct from boot.uefi, which builds GRUB *into* the ISO's own EFI System Partition.
    This one produces a fragment to paste into /etc/grub.d/40_custom on a machine that
    already boots something else -- the least invasive way to keep Slax on a working
    system, because nothing is overwritten and no MBR is touched.

    The docs previously told people to hand-write this in three places and the three
    disagreed with each other (one had --no-floppy, the parameter order differed).
    Generating it from the real menu entries means it cannot drift from what the ISO
    actually boots.
    """
    dest_rel = step.get("dest") or "slax/boot/grub-snippet.cfg"
    src_cfg = ctx.p("slax", "boot", step.get("from") or "syslinux.cfg")
    if not os.path.isfile(src_cfg):
        src_cfg = ctx.p("slax", "boot", "isolinux.cfg")
    if not os.path.isfile(src_cfg):
        raise RuntimeError("boot.grub: no syslinux.cfg or isolinux.cfg to mirror")
    entries = _parse_syslinux(src_cfg)
    if not entries:
        raise RuntimeError(f"boot.grub: no usable LABEL entries in {src_cfg}")

    # search --file --set=root is what makes the snippet portable: it locates whichever
    # device holds /slax/boot/vmlinuz rather than hardcoding (hd0,1), so the same text
    # works after the disk is repartitioned or the stick moves.
    probe = step.get("probe") or "/slax/boot/vmlinuz"
    out = [
        "# Generated by slax-kitchen boot.grub -- paste into /etc/grub.d/40_custom on the",
        "# HOST system (not this ISO), then run update-grub / grub-mkconfig.",
        "#",
        "# `search --file` finds whichever device carries Slax, so this keeps working if",
        "# the disk is repartitioned or the stick is moved to another port.",
        "",
    ]
    for e in entries:
        title = e.get("menu_label", e["label"]).replace("'", "'\\''")
        append = e.get("append", "")
        initrd = ""
        m = re.search(r"initrd=(\S+)", append)
        if m:
            initrd = m.group(1)
            append = append.replace(m.group(0), "")
        # Collapse AFTER removing initrd=, or its removal leaves a double space behind.
        append = re.sub(r"\s+", " ", append).strip()
        out.append(f"menuentry '{title}' {{")
        out.append(f"    search --no-floppy --file --set=root {probe}")
        out.append(f"    linux  {e['kernel']} {append}".rstrip())
        if initrd:
            out.append(f"    initrd {initrd}")
        out.append("}")
        out.append("")
    text = "\n".join(out)

    if ctx.dry:
        ctx.say(f"would write {dest_rel} ({len(entries)} entries)")
        return

    dest = _under(ctx.tree, dest_rel, "boot.grub")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        f.write(text)

    # grub-script-check is to GRUB what `sh -n` is to the initramfs scripts: it parses
    # the file and fails on a syntax error, so a malformed snippet is caught here rather
    # than by a user whose boot menu has silently lost an entry.
    if shutil.which("grub-script-check"):
        r = subprocess.run(["grub-script-check", dest], capture_output=True, text=True)
        if r.returncode != 0:
            os.unlink(dest)
            raise RuntimeError("boot.grub: generated snippet does not parse as GRUB:\n  "
                               + r.stderr.strip()[:300])
        ctx.say("grub-script-check: ok")
    else:
        ctx.say("warning: grub-script-check not installed; snippet not validated")
    ctx.say(f"wrote {dest_rel} ({len(entries)} entries mirrored from "
            f"{os.path.basename(src_cfg)})")
    ctx.changes.append(dest_rel)


@verb("boot.uefi")
def v_boot_uefi(ctx: Ctx, step: dict) -> None:
    """Make the ISO bootable on UEFI firmware.

    The stock ISO cannot boot on UEFI at all: its El Torito catalog has exactly one
    entry, platform 0x00 (BIOS). /slax/boot/EFI/Boot/bootx64.efi exists but is dead
    weight there -- it is syslinux.efi, which can only read FAT, and bootinst.sh moves
    it to the root of a FAT USB stick. On an ISO9660 filesystem it is unreachable.

    So we add a second El Torito entry pointing at a real FAT EFI System Partition, and
    put GRUB in it instead of syslinux.efi, because GRUB *can* read iso9660: a ~4 MiB
    ESP holding only BOOTX64.EFI is enough, and the kernel stays on the ISO filesystem
    where it already is. Building the ESP with mkfs.vfat -C plus mtools means nothing
    is ever mounted, so this works unprivileged.
    """
    for tool in ("grub-mkstandalone", "mkfs.vfat", "mmd", "mcopy"):
        if not shutil.which(tool):
            raise RuntimeError(f"boot.uefi: {tool} not installed")

    src_cfg = ctx.p("slax", "boot", "isolinux.cfg")
    if not os.path.isfile(src_cfg):
        raise RuntimeError("boot.uefi: slax/boot/isolinux.cfg missing")
    entries = _parse_syslinux(src_cfg)
    if not entries:
        raise RuntimeError("boot.uefi: no usable LABEL entries found in isolinux.cfg")

    grub_dir = ctx.p("boot", "grub")
    cfg_text = _grub_cfg(entries)
    if ctx.dry:
        ctx.say(f"would build an ESP and mirror {len(entries)} menu entries into GRUB")
        ctx.hint("uefi", True)
        return
    os.makedirs(grub_dir, exist_ok=True)
    open(os.path.join(grub_dir, "grub.cfg"), "w").write(cfg_text)
    ctx.say(f"boot/grub/grub.cfg: mirrored {len(entries)} menu entr"
            f"{'y' if len(entries) == 1 else 'ies'} from isolinux.cfg")

    import tempfile
    tmp = tempfile.mkdtemp(prefix="kitchen-esp-")
    try:
        # The embedded config only needs to find the real one on the ISO filesystem.
        early = os.path.join(tmp, "early.cfg")
        open(early, "w").write(
            "search --no-floppy --file --set=root /slax/boot/vmlinuz\n"
            "configfile ($root)/boot/grub/grub.cfg\n")
        efi = os.path.join(tmp, "BOOTX64.EFI")
        r = subprocess.run(
            ["grub-mkstandalone", "-O", "x86_64-efi", "-o", efi,
             "--modules=" + GRUB_MODULES, f"boot/grub/grub.cfg={early}"],
            capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(efi):
            raise RuntimeError("grub-mkstandalone failed: " + (r.stderr.strip() or "?"))
        efi_kib = (os.path.getsize(efi) + 1023) // 1024

        # FAT12 needs slack beyond the payload for its reserved/root/FAT areas.
        img_kib = max(1440, ((efi_kib + 256) // 64 + 1) * 64)
        img = ctx.p("boot", "efi.img")
        os.makedirs(os.path.dirname(img), exist_ok=True)
        for cmd in (["mkfs.vfat", "-C", "-F", "12", "-n", "SLAXEFI", img, str(img_kib)],
                    ["mmd", "-i", img, "::/EFI", "::/EFI/BOOT"],
                    ["mcopy", "-i", img, efi, "::/EFI/BOOT/BOOTX64.EFI"]):
            rr = subprocess.run(cmd, capture_output=True, text=True)
            if rr.returncode != 0:
                raise RuntimeError(f"{cmd[0]} failed: {rr.stderr.strip()}")
        ctx.say(f"boot/efi.img: {img_kib} KiB FAT12 ESP containing "
                f"EFI/BOOT/BOOTX64.EFI ({efi_kib} KiB GRUB)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ctx.hint("uefi", True)



# Paths that must never enter an output bundle. Mirrors the EXCLUDE list in upstream's
# /usr/bin/savechanges, plus the scaffolding we add for the build itself. Getting this
# wrong ships a bundle that clobbers the live system's /etc/fstab or /etc/resolv.conf.
BUNDLE_EXCLUDE = re.compile(
    # Runtime dirs: livekit's change_root() creates these at boot. We only make them so
    # the chroot works; shipping them would be at best pointless.
    r"^(boot|dev|mnt|proc|run|sys|tmp)(/|$)"
    # Caches and logs, per upstream savechanges' own exclude list.
    r"|^var/(cache|backups|tmp|log|lock)(/|$)"
    # Package-manager METADATA caches. These are large (slackpkg's filelist alone is 4 MB,
    # its ChangeLog another 2 MB) and are regenerated by `apt-get update` / `slackpkg
    # update` anyway. Missing the slackpkg one shipped 9 MB of junk in a bundle whose only
    # real payload was one binary.
    r"|^var/lib/(apt|slackpkg)(/|$)"
    # Lock files.
    r"|^var/lib/dpkg/(lock|lock-frontend|triggers/Lock)$"
    # shadow-utils' lock and its backup copies. useradd/chpasswd write passwd-, shadow-,
    # group-, gshadow-, subuid- and subgid- holding the state BEFORE the change, so a
    # recipe whose whole purpose is changing /etc/shadow would otherwise ship the old one
    # beside the new one -- and .pwd.lock is a lock, never content.
    r"|^etc/\.pwd\.lock$"
    r"|^etc/(passwd|shadow|group|gshadow|subuid|subgid)-$"
    # Generated at boot by livekit -- a bundle copy would clobber the live system's.
    r"|^etc/(resolv\.conf|mtab|fstab)$|^etc/ld\.so\.cache$"
    # Scaffolding WE add for the build. Shipping these would push our build-time config
    # onto the running system: policy-rc.d would block every service start, and the
    # slackpkg files carry our dead-repo pruning and any pinned mirror.
    r"|^usr/sbin/policy-rc\.d$"
    r"|^etc/apt/apt\.conf\.d/00kitchen$"
    r"|^etc/slackpkg/(mirrors|slackpkgplus\.conf)$"
    # Build-time droppings in root's home: the GPG keyring slackpkg builds while
    # verifying signatures, and wget's HSTS cache. These patterns must end in
    # (/|$), not a bare / -- otherwise the empty DIRECTORY entry still ships even
    # though every file inside it is excluded.
    r"|^root/(\.gnupg(/|$)|\.wget-hsts$)"
    # aufs whiteouts.
    r"|^\.wh\."
)

# livekit's change_root() creates these at boot; a bundle does not contain them, so a
# chroot build has to make them or apt dies with "Unable to mkstemp /tmp/...".
RUNTIME_DIRS = ["boot", "dev", "proc", "sys", "tmp", "media", "mnt", "run"]
RUNTIME_NODES = [("null", 1, 3), ("zero", 1, 5), ("full", 1, 7), ("random", 1, 8),
                 ("urandom", 1, 9), ("tty", 5, 0), ("console", 5, 1)]


def _manifest(root: str) -> dict:
    """Snapshot every entry as (type, size, mtime_ns, mode).

    Size+mtime+mode, not just names: a filename-only diff misses MODIFIED files, and the
    most important modified file is var/lib/dpkg/status. Ship a bundle without it and the
    new binaries are invisible to the package database. Upstream's savechanges never hits
    this because aufs copy-up puts modified files physically in the changes layer.
    """
    out = {}
    rl = len(root.rstrip("/")) + 1
    for dirpath, dirnames, filenames in os.walk(root):
        for n in list(dirnames) + filenames:
            p = os.path.join(dirpath, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            out[p[rl:]] = (os.path.isdir(p) and not os.path.islink(p),
                           st.st_size, st.st_mtime_ns, st.st_mode)
    return out


def _prepare_chroot(root: str) -> None:
    for d in RUNTIME_DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    os.chmod(os.path.join(root, "tmp"), 0o1777)
    for name, major, minor in RUNTIME_NODES:
        dev = os.path.join(root, "dev", name)
        if not os.path.exists(dev):
            try:
                os.mknod(dev, 0o600 | 0o020000, os.makedev(major, minor))
                os.chmod(dev, 0o666)
            except OSError as e:
                raise RuntimeError(
                    f"cannot create {dev}: {e}. bundle.packages needs CAP_MKNOD.") from e
    # Stop maintainer scripts from trying to start services in the chroot.
    prc = os.path.join(root, "usr", "sbin", "policy-rc.d")
    os.makedirs(os.path.dirname(prc), exist_ok=True)
    with open(prc, "w") as f:
        f.write("#!/bin/sh\nexit 101\n")
    os.chmod(prc, 0o755)
    # The bundle's resolv.conf points at 8.8.8.8, which may not be reachable.
    if os.path.isfile("/etc/resolv.conf"):
        shutil.copy2("/etc/resolv.conf", os.path.join(root, "etc", "resolv.conf"))


def _in_chroot(root: str, argv: list[str], env: dict | None = None,
               stdin: str | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update({"DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C", "LANG": "C",
              "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    if env:
        e.update(env)
    return subprocess.run(["chroot", root] + argv, capture_output=True, text=True,
                          env=e, input=stdin)


def _installed(root: str, flavour: str, pkg: str) -> bool:
    """Is the package actually present in the target's package database?

    Exit codes are not trustworthy here. slackpkg returns 0 after declining its own
    confirmation prompt and installing nothing, which produced a cheerful "success"
    and a 4 KiB bundle containing no packages. Always verify against the database.
    """
    if flavour == "debian":
        r = _in_chroot(root, ["dpkg-query", "-W", "-f=${Status}", pkg])
        return r.returncode == 0 and "install ok installed" in r.stdout
    # Slax's /var/log/packages is a symlink to /var/lib/pkgtools/packages; try both.
    pkgdir = os.path.join(root, "var", "lib", "pkgtools", "packages")
    if not os.path.isdir(pkgdir):
        pkgdir = os.path.join(root, "var", "log", "packages")
    if not os.path.isdir(pkgdir):
        return False
    # Entries are <name>-<version>-<arch>-<build>, and <name> may itself contain
    # hyphens (gcc-g++). Strip exactly the last three fields rather than prefix
    # matching, or "gcc" would match "gcc-g++-13.2.0-x86_64-1".
    for n in os.listdir(pkgdir):
        if n.rsplit("-", 3)[0] == pkg:
            return True
    return False


def _prune_dead_repos(root: str, say) -> None:
    """Drop unreachable slackpkg+ repositories before running slackpkg.

    Slax 15.0.4 ships REPOPLUS=( slackpkgplus slackonly ), and slackonly.com is
    NXDOMAIN as of 2026. slackpkg update fails outright on a dead repo, so a stock
    Slax Slackware base cannot install anything without this. Repo rot only gets
    worse for a distro release that has been dormant for years.
    """
    conf = os.path.join(root, "etc", "slackpkg", "slackpkgplus.conf")
    if not os.path.isfile(conf):
        return
    text = open(conf).read()
    m = re.search(r"^REPOPLUS=\(([^)]*)\)", text, re.M)
    if not m:
        return
    repos = m.group(1).split()
    mirrors = dict(re.findall(r"^MIRRORPLUS\['([^']+)'\]=(\S+)", text, re.M))

    import socket
    alive, dead = [], []
    for repo in repos:
        url = mirrors.get(repo, "")
        host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
        if not host:
            alive.append(repo)
            continue
        try:
            socket.setdefaulttimeout(6)
            socket.getaddrinfo(host, None)
            alive.append(repo)
        except OSError:
            dead.append(f"{repo} ({host})")
    if not dead:
        return
    text = re.sub(r"^REPOPLUS=\([^)]*\)", "REPOPLUS=( " + " ".join(alive) + " )",
                  text, count=1, flags=re.M)
    with open(conf, "w") as f:
        f.write(text)
    say(f"pruned unreachable slackpkg+ repo(s): {', '.join(dead)}")


def _detect_flavour(tree: str) -> str:
    mods = os.path.join(tree, "slax", "modules")
    for n in sorted(os.listdir(mods)) if os.path.isdir(mods) else []:
        if n.startswith("01-core"):
            r = subprocess.run(["unsquashfs", "-l", os.path.join(mods, n)],
                               capture_output=True, text=True)
            if "slackware-version" in r.stdout:
                return "slackware"
            if "debian_version" in r.stdout:
                return "debian"
    return "debian"


@verb("bundle.packages")
def v_bundle_packages(ctx: Ctx, step: dict) -> None:
    """Install distro packages into a NEW bundle, built from the ISO's own bundles.

    01-core.sb IS a complete Debian 12 / Slackware 15 root filesystem with apt / pkgtools
    already in it, so no debootstrap and no external base image is needed -- and the
    result is guaranteed to match the shipped kernel's ABI.

    Needs real chroot (CAP_SYS_CHROOT + CAP_MKNOD). proot is NOT an acceptable
    substitute: proot 5.1.0 does not translate statx(), so stat escapes the rootfs and
    reads the host filesystem, silently and selectively. See docs/40-workflow/edit-bundles.md.
    """
    for tool in ("unsquashfs", "mksquashfs", "chroot"):
        if not shutil.which(tool):
            raise RuntimeError(f"bundle.packages: {tool} not installed")

    packages = step.get("packages") or []
    if not packages:
        raise RuntimeError("bundle.packages: no packages listed")
    out_name = step.get("bundle") or "07-packages.sb"
    if not out_name.endswith(".sb"):
        out_name += ".sb"
    if not re.match(r"^\d\d-", out_name):
        raise RuntimeError(f"bundle.packages: bundle name must start with NN- "
                           f"(load order is the numeric prefix); got {out_name!r}")

    mods = ctx.p("slax", "modules")
    stack = step.get("from") or ["01-core"]
    flavour = step.get("flavour") or _detect_flavour(ctx.tree)

    if ctx.dry:
        ctx.say(f"would install {', '.join(packages)} into {out_name} "
                f"(flavour={flavour}, stack={'+'.join(stack)})")
        return

    import tempfile
    build = tempfile.mkdtemp(prefix="kitchen-pkg-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    root = os.path.join(build, "root")
    try:
        # 1. Stack the source bundles in numeric order so later ones win, exactly as
        #    the union does at boot.
        os.makedirs(root, exist_ok=True)
        picked = []
        for want in stack:
            hit = next((n for n in sorted(os.listdir(mods))
                        if n.startswith(want) and n.endswith(".sb")), None)
            if hit is None:
                raise RuntimeError(f"bundle.packages: no bundle matching {want!r} in slax/modules")
            picked.append(hit)
        for n in picked:
            r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", root,
                                os.path.join(mods, n)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"unsquashfs {n}: {r.stderr.strip()[:300]}")
        ctx.say(f"unpacked {' + '.join(picked)} as the build root")

        # 2. Make it a usable root filesystem (see RUNTIME_DIRS).
        _prepare_chroot(root)

        # 3. Snapshot, install, snapshot.
        before = _manifest(root)
        if flavour == "debian":
            with open(os.path.join(root, "etc/apt/apt.conf.d/00kitchen"), "w") as f:
                f.write('APT::Sandbox::User "root";\nAcquire::Retries "3";\n')
            if step.get("apt", {}).get("update", True):
                r = _in_chroot(root, ["apt-get", "update", "-qq"])
                if r.returncode != 0:
                    raise RuntimeError("apt-get update failed:\n" + r.stderr.strip()[-1500:])
            argv = ["apt-get", "install", "-y", "-qq"]
            if step.get("apt", {}).get("no_recommends", True):
                argv.append("--no-install-recommends")
            r = _in_chroot(root, argv + list(packages))
        elif flavour == "slackware":
            # slackpkg + slackpkg+ are preconfigured in Slax's 01-core, but -batch=on
            # does NOT cover slackpkg's "you picked a -current mirror but 15.0+ is
            # installed, is this really what you want?" confirmation. With no stdin it
            # takes the default (No), installs nothing and still exits 0. Feed it "y".
            mirrors = os.path.join(root, "etc", "slackpkg", "mirrors")
            pin = step.get("mirror")
            if pin:
                with open(mirrors, "w") as f:
                    f.write(f"# pinned by slax-kitchen\n{pin}\n")
                ctx.say(f"pinned slackpkg mirror to {pin}")
            elif os.path.isfile(mirrors):
                active = [ln.strip() for ln in open(mirrors)
                          if ln.strip() and not ln.strip().startswith("#")]
                if any("-current" in m for m in active):
                    ctx.say("warning: mirror is Slackware -current, which has moved well past "
                            "this 2023 base -- packages may link against a newer glibc than "
                            "the bundle ships. Set `mirror:` on the step to pin one.")
            _prune_dead_repos(root, ctx.say)
            yes = "y\n" * 200
            r = _in_chroot(root, ["slackpkg", "-batch=on", "-default_answer=y", "update"],
                           stdin=yes)
            # slackpkg's exit code is unreliable in BOTH directions: 0 after declining its
            # own confirmation and doing nothing, and 1 after a perfectly good update
            # (it returns 1 while printing the "you are running 15.0+, be sure to run
            # these steps" advisory). Judge it by whether metadata actually landed.
            pkgtxt = os.path.join(root, "var", "lib", "slackpkg", "PACKAGES.TXT")
            if not (os.path.isfile(pkgtxt) and os.path.getsize(pkgtxt) > 1024):
                raise RuntimeError(
                    "slackpkg update produced no package metadata "
                    f"(exit {r.returncode}):\n" + (r.stdout or r.stderr).strip()[-1500:])
            ctx.say(f"slackpkg metadata: {os.path.getsize(pkgtxt) // 1024} KiB "
                    f"PACKAGES.TXT (slackpkg exited {r.returncode}, which it does even "
                    f"on success)")
            r = _in_chroot(root, ["slackpkg", "-batch=on", "-default_answer=y",
                                  "install"] + list(packages), stdin=yes)
        else:
            raise RuntimeError(f"bundle.packages: unknown flavour {flavour!r}")
        if r.returncode != 0:
            raise RuntimeError(f"package install failed (exit {r.returncode}):\n"
                               + (r.stderr.strip() or r.stdout.strip())[-1500:])

        # Never trust the exit code alone -- see _installed().
        missing = [p for p in packages if not _installed(root, flavour, p)]
        if missing:
            tail = (r.stdout.strip() or r.stderr.strip())[-1200:]
            raise RuntimeError(
                f"package manager reported success but these are not installed: "
                f"{', '.join(missing)}\n--- last output ---\n{tail}")
        ctx.say(f"verified installed: {', '.join(packages)}")
        after = _manifest(root)

        # 4. Delta = added OR modified. Names alone are not enough.
        added = [k for k in after if k not in before]
        modified = [k for k in after if k in before and after[k] != before[k]]
        keep = sorted(k for k in added + modified if not BUNDLE_EXCLUDE.search(k))
        ctx.say(f"delta: {len(added)} added, {len(modified)} modified, "
                f"{len(keep)} kept after exclusions")
        if not keep:
            raise RuntimeError("bundle.packages: nothing to package "
                               "(were the packages already present in the base?)")

        # 5. Stage just the delta, preserving parent directory metadata.
        stage = os.path.join(build, "stage")
        for rel in keep:
            src, dst = os.path.join(root, rel), os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.islink(src):
                lnk = os.readlink(src)
                if not os.path.lexists(dst):
                    os.symlink(lnk, dst)
            elif os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                shutil.copystat(src, dst)
            else:
                shutil.copy2(src, dst)
                st = os.lstat(src)
                os.chown(dst, st.st_uid, st.st_gid)

        # 6. Build with upstream's exact parameters (livekitlib create_bundle / dir2sb).
        target = os.path.join(mods, out_name)
        r = subprocess.run(["mksquashfs", stage, target, "-comp", "xz", "-b", "1024K",
                            "-Xbcj", "x86", "-always-use-fragments", "-noappend"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"mksquashfs failed: {r.stderr.strip()[:300]}")
        ctx.say(f"built slax/modules/{out_name} "
                f"({os.path.getsize(target) // 1024} KiB, {len(keep)} paths)")
    finally:
        shutil.rmtree(build, ignore_errors=True)


# ------------------------------------------------------------- engine --------

def _tree_facts(work: str, tree: str) -> dict:
    """Facts a recipe can branch on with `when:`."""
    import yaml
    facts = {"flavour": _detect_flavour(tree), "arch": "unknown"}
    origin = os.path.join(work, ".kitchen", "origin.yaml")
    if os.path.isfile(origin):
        src = str((yaml.safe_load(open(origin)) or {}).get("source_iso", ""))
        if "32bit" in src:
            facts["arch"] = "32bit"
        elif "64bit" in src:
            facts["arch"] = "64bit"
    return facts


def _when_ok(expr: str, facts: dict) -> bool:
    """Evaluate a `when:` guard. Deliberately tiny: `key==value` or `key!=value`.

    Not a general expression language -- a recipe is config, and anything needing
    real logic belongs in a separate recipe rather than a mini-DSL nobody can audit.
    """
    m = re.match(r"^\s*([a-z_]+)\s*(==|!=)\s*(\S+)\s*$", expr)
    if not m:
        raise RuntimeError(f"cannot parse when: {expr!r} (want 'key==value' or 'key!=value')")
    key, op, want = m.group(1), m.group(2), m.group(3).strip("'\"")
    if key not in facts:
        raise RuntimeError(f"when: unknown fact {key!r} (have: {', '.join(sorted(facts))})")
    return (facts[key] == want) if op == "==" else (facts[key] != want)


def load_recipe(path: str) -> dict:
    import yaml
    problems = validate_file(path)
    if problems:
        raise RuntimeError("invalid recipe:\n  " + "\n  ".join(problems))
    return yaml.safe_load(open(path))


def resolve(names: list[str], search: list[str]) -> list[str]:
    """Turn recipe names/paths into paths, pulling in compat.requires first."""
    import yaml
    out: list[str] = []
    seen: set[str] = set()

    def find(n: str) -> str:
        if os.path.isfile(n):
            return n
        for d in search:
            for ext in (".yaml", ".yml"):
                p = os.path.join(d, n + ext)
                if os.path.isfile(p):
                    return p
        raise RuntimeError(f"recipe not found: {n} (searched {', '.join(search)})")

    def walk(n: str, stack: tuple) -> None:
        p = find(n)
        key = os.path.abspath(p)
        if key in seen:
            return
        base = os.path.basename(p).rsplit(".", 1)[0]
        if base in stack:
            raise RuntimeError("recipe dependency cycle: " + " -> ".join(stack + (base,)))
        doc = yaml.safe_load(open(p)) or {}
        for dep in (doc.get("compat", {}) or {}).get("requires", []) or []:
            walk(dep, stack + (base,))
        seen.add(key)
        out.append(p)

    for n in names:
        walk(n, ())
    return out


def check_compat(doc: dict, work: str) -> list[str]:
    """Warn (not fail) when a recipe does not declare support for this base."""
    import yaml
    origin = os.path.join(work, ".kitchen", "origin.yaml")
    if not os.path.isfile(origin):
        return []
    info = yaml.safe_load(open(origin)) or {}
    src = str(info.get("source_iso", ""))
    compat = doc.get("compat", {}) or {}
    warn = []
    flav = "slackware" if "slackware" in src else "debian" if "debian" in src else None
    arch = "32bit" if "32bit" in src else "64bit" if "64bit" in src else None
    if flav and compat.get("flavours") and flav not in compat["flavours"]:
        warn.append(f"recipe declares flavours={compat['flavours']} but the base looks like {flav}")
    if arch and compat.get("arch") and arch not in compat["arch"]:
        warn.append(f"recipe declares arch={compat['arch']} but the base looks like {arch}")
    return warn


def plan_recipe(path: str, facts: dict) -> tuple[dict, list[tuple[int, dict, bool]]]:
    """Resolve a recipe into (doc, [(index, step, will_run)]).

    Shared by preflight and apply so they cannot disagree about which steps run --
    demanding a tool for a step that `when:` is going to skip would be its own bug.
    """
    doc = load_recipe(path)
    vars_ = dict(doc.get("vars", {}) or {})
    steps = []
    for i, raw in enumerate(doc["steps"], 1):
        step = subst(raw, vars_)
        run = "when" not in step or _when_ok(step["when"], facts)
        steps.append((i, step, run))
    return doc, steps


def apply_recipe(path: str, work: str, dry: bool = False) -> int:
    facts = _tree_facts(work, os.path.join(work, "iso"))
    doc, steps = plan_recipe(path, facts)
    name = doc["metadata"]["name"]
    ctx = Ctx(work, os.path.dirname(os.path.abspath(path)), name, dry)
    ctx.facts = facts
    print(f"  {name}: {doc['metadata']['summary']}")
    for w in check_compat(doc, work):
        print(f"    warning: {w}", file=sys.stderr)

    for i, step, run in steps:
        if not run:
            print(f"    skip step {i} ({step['verb']}): when {step['when']} is false "
                  f"[{', '.join(f'{k}={v}' for k, v in sorted(ctx.facts.items()))}]")
            continue
        v = step["verb"]
        fn = VERBS.get(v)
        if fn is None:
            if v in WONT_DO:
                raise RuntimeError(f"step {i}: verb '{v}' will not be implemented -- "
                                   f"{WONT_DO[v]}")
            raise RuntimeError(f"step {i}: verb '{v}' is valid in the schema but not "
                               f"implemented yet (have: {', '.join(sorted(VERBS))})")
        fn(ctx, step)

    if not dry:
        import yaml
        jpath = os.path.join(ctx.meta, "journal.yaml")
        j = (yaml.safe_load(open(jpath)) if os.path.isfile(jpath) else None) or {"applied": []}
        j["applied"].append({"recipe": name, "changes": ctx.changes})
        with open(jpath, "w") as f:
            yaml.safe_dump(j, f, sort_keys=False)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="apply recipes to a work tree")
    ap.add_argument("recipes", nargs="+")
    ap.add_argument("-w", "--work", default="work")
    ap.add_argument("-n", "--dry-run", action="store_true")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="do not check tools/capabilities first (not recommended)")
    ap.add_argument("--preflight-only", action="store_true",
                    help="check requirements and exit; touches nothing")
    ap.add_argument("--facts", metavar="k=v,k=v",
                    help="override the facts used for `when:` guards. kitchen build uses "
                         "this to preflight from the profile BEFORE unpacking 400+ MiB")
    a = ap.parse_args(argv[1:])
    override = {}
    if a.facts:
        for pair in a.facts.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                override[k.strip()] = v.strip()
    if not a.preflight_only and not os.path.isdir(os.path.join(a.work, "iso")):
        print(f"no work tree at {a.work}/iso (run 'kitchen unpack' first)", file=sys.stderr)
        return 2
    search = [os.path.join(ROOT, "recipes", "available"),
              os.path.join(ROOT, "recipes", "examples"), os.getcwd()]
    try:
        paths = resolve(a.recipes, search)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.preflight_only:
        print(f"preflight {len(paths)} recipe(s)"
              + (f"  [{a.facts}]" if a.facts else ""))
    else:
        print(f"apply {len(paths)} recipe(s) to {a.work}"
              + ("  [dry run]" if a.dry_run else ""))

    # Check everything the WHOLE plan needs before touching anything. Without this the
    # first three recipes apply, download files and edit configs, and the fourth dies on
    # a missing tool -- leaving a half-modified tree.
    if not a.skip_preflight:
        facts = dict(override) if override else _tree_facts(a.work, os.path.join(a.work, "iso"))
        plan: list[tuple[str, dict]] = []
        for p in paths:
            try:
                doc, steps = plan_recipe(p, facts)
            except RuntimeError as e:
                print(f"error: {os.path.basename(p)}: {e}", file=sys.stderr)
                return 2
            for _i, step, run in steps:
                if run:
                    plan.append((doc["metadata"]["name"], step))
        problems = preflight(plan)
        if problems:
            # stdout is block-buffered when piped; without this the error lands above
            # its own header and reads as if it happened first.
            sys.stdout.flush()
            print(f"\npreflight failed -- {len(problems)} unmet requirement(s), "
                  "nothing has been modified:", file=sys.stderr)
            for pr in problems:
                print(f"  - {pr}", file=sys.stderr)
            print("\nRun `kitchen doctor` for the full tool and capability report.",
                  file=sys.stderr)
            sys.stderr.flush()
            return 2
        print(f"  preflight ok ({len(plan)} steps)")
    if a.preflight_only:
        return 0

    for p in paths:
        try:
            apply_recipe(p, a.work, a.dry_run)
        except Exception as e:                       # noqa: BLE001
            print(f"error: {os.path.basename(p)}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
