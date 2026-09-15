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
import stat
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dpkgdb  # noqa: E402
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

# Verbs the schema accepts that are planned but not written. Same reasoning as WONT_DO:
# `kitchen validate` passing and `kitchen apply` then failing is bad enough without the
# failure also being uninformative about which of the two it is.
NOT_YET = {
    "kernel.replace": (
        "replacing the kernel means rebuilding the initramfs against the new module "
        "set and proving aufs is still present -- without it the union silently "
        "downgrades to overlayfs and `slax activate` stops working. See "
        "docs/00-overview/status.md."),
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


# Verbs that fetch their `src` over the network when it is a URL. Anything added here
# must actually urlopen a `src`; anything that urlopens and is NOT here is a preflight
# that lies, which is what tests/unit/test_apply.py checks against the AST.
_URL_SRC_VERBS = ("boot.payload", "bundle.fromTarball")


def step_requires(step: dict) -> dict:
    """Requirements for one step, including the ones that depend on its arguments."""
    req = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
           for k, v in VERB_REQUIRES.get(step.get("verb", ""), {}).items()}
    # These verbs urlopen their `src`, so they need the network only when it is a URL.
    # bundle.fromTarball was missing here while doing the identical fetch: preflight said
    # nothing, and `kitchen build` unpacked 436 MiB before dying at the download.
    # test_network_is_declared_where_it_is_used keeps the list honest against the code.
    if step.get("verb") in _URL_SRC_VERBS and re.match(r"^https?://", str(step.get("src", ""))):
        req["network"] = True
    # `network: true` on any step. bundle.script's docstring has promised this for
    # months and nothing read it, because $defs/step was open and the key validated
    # silently. It DECLARES intent, checked at preflight; it does not sandbox anything,
    # and _in_chroot cannot -- isolating the network needs a user namespace, which the
    # environments this runs in do not all have.
    if step.get("network"):
        req["network"] = True
    return req



# Verbs that build a bundle against whatever is already beneath it.
_STACKING_VERBS = ("bundle.packages", "bundle.script")


# Verbs that move a bundle out from under one already built. bundle.renumber belongs
# here as much as bundle.remove: renumber-bundles.yaml ships `^05-chromium` -> 95 as its
# default, which leaves chromium in place while the bundle above is built and THEN lifts
# it over the top -- so the build is against it and the finished image is not.
_DISTURBING_VERBS = ("bundle.remove", "bundle.renumber")


def _bundle_number(name: str) -> int | None:
    """The numeric prefix of a bundle name, or None if there is not one."""
    m = re.match(r"^(\d+)", name)
    return int(m.group(1)) if m else None


def _only_above(step: dict, built: list[tuple[str, str]]) -> bool:
    """True when this step can only touch bundles that no built bundle stacks on.

    _bundle_stack drops EVERYTHING sorting at or above the bundle being built, not just
    98 and 99 -- so removing a higher-numbered bundle afterwards is provably safe and
    refusing it is pure friction. Decidable from the step list alone whenever the regex
    is anchored on a literal number, which is how every shipped recipe writes it.

    Conservative by construction: an unanchored or templated `match` yields None and the
    step is treated as disturbing, because a regex can match anything.
    """
    m = re.match(r"^\^(\d+)", str(step.get("match", "")))
    if not m:
        return False
    target = int(m.group(1))
    numbers = [n for n in (_bundle_number(b) for _r, b in built) if n is not None]
    return bool(numbers) and target > max(numbers)


def _built_before(work: str | None) -> list[tuple[str, str]]:
    """Bundles this tree was already given, from the journal, still present on disk.

    Without this the rule holds inside one invocation and nowhere else: the two
    one-liners every cookbook page documents -- `kitchen apply firefox-esr` then
    `kitchen apply remove-chromium` -- both exit 0 and produce exactly the state the
    single-invocation refusal exists to prevent.

    The journal is the right source and the filesystem is not: it records what earlier
    runs BUILT, which is the question, where `slax/modules/` only says what is there now.
    Intersecting the two drops anything built and since removed. lib/status.py does the
    same reconstruction to mark which bundles are ours.
    """
    if not work:
        return []
    import yaml
    mods = os.path.join(work, "iso", "slax", "modules")
    try:
        with open(os.path.join(work, ".kitchen", "journal.yaml")) as fh:
            j = yaml.safe_load(fh) or {}
        present = set(os.listdir(mods))
    except (OSError, yaml.YAMLError):
        return []
    out = []
    for e in j.get("applied") or []:
        for art in e.get("artifacts") or []:
            base = os.path.basename(art)
            if art.startswith("-") or not base.endswith(".sb") or base not in present:
                continue
            out.append((f"{e.get('recipe', '?')} (earlier run)", base))
    return out


def check_plan_order(plan: list[tuple[str, dict]], work: str | None = None) -> list[str]:
    """bundle.remove and bundle.renumber must precede bundle.packages / bundle.script.

    `from:` defaults to the whole stack below the bundle being built, which is the right
    default and the reason this rule exists. A bundle built that way assumes everything
    beneath it still exists at boot; disturbing one of those afterwards leaves binaries
    with an unresolvable NEEDED. Nothing detects it -- the file delta is empty for the
    missing libraries, so _write_fragment does not declare them either and the merged
    98-dpkg-db.sb stays self-consistent. The image builds, passes every gate, and fails
    when a user runs the program.

    Renumbering is the same class and is worse in one way: a remove at least takes the
    bundle out of the next _bundle_stack, while a renumber leaves it in place for the
    build and only then lifts it above. Move a real dpkg status above an add-on's
    fragment and dpkgdb.merge_tree discards the fragment outright.

    THE SCOPE OF THE RULE, and what it needs to see:

    * Within one plan, it is decidable from the step list alone.
    * Across invocations it is not, and plan-only was not enough -- `kitchen apply
      firefox-esr` then `kitchen apply remove-chromium` is what every cookbook page
      documents, and both exited 0. `work` seeds the prior bundles from the journal.
    * Under --preflight-only there is no tree yet, because `kitchen build` preflights
      before it unpacks (lib/build.sh). `work` is None there and the rule is plan-only,
      which is what it always was. The journal, not the filesystem, is what makes this
      safe: `slax/modules/` before a run still holds 05-chromium.sb, so a filesystem
      check would flag chromium-current -- the one shipped recipe that is deliberately
      correct. The journal records what earlier runs BUILT, which is the real question.

    Still stricter than necessary for 98-dpkg-db.sb and 99-changes-N, which no default
    stack contains. _only_above covers the general form of that -- _bundle_stack drops
    everything sorting at or above the target, not merely those two -- but only when the
    match is anchored on a literal number. Reordering fixes the rest, harmlessly.
    """
    built: list[tuple[str, str]] = list(_built_before(work))
    problems = []
    for recipe, step in plan:
        verb = step.get("verb")
        if verb in _STACKING_VERBS:
            built.append((recipe, str(step.get("bundle", "?"))))
        elif verb in _DISTURBING_VERBS and built and not _only_above(step, built):
            who, bundle = built[0]
            # Attribution rides on the bundle, not the recipe: "in X, after Y runs after
            # Z was built" said `after` twice in one clause.
            by = "" if recipe == who else f" by {who}"
            # REORDERING IS THE ONLY REMEDY THIS FUNCTION HONOURS, so it is the only one
            # named. An earlier version also offered "say so with an explicit from: on
            # the build step" -- advice that did nothing, because nothing here reads
            # from:. Honouring it would mean deciding whether any bundle `match` matches
            # could start with any prefix in `from:`, which the step list cannot answer.
            problems.append(
                f"{verb} (match {step.get('match', '?')!r}) in {recipe} runs after "
                f"{bundle} was built{by}. A bundle takes everything below it as given, so "
                f"disturbing one afterwards can leave an unresolvable NEEDED that no gate "
                f"can see. Put every bundle.remove and bundle.renumber before every "
                f"bundle.packages / bundle.script -- chromium-current does, deliberately, "
                f"and removing first also makes the remaining from: stacks come out right "
                f"on their own.")
    return problems


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
        """Tell the user. Does NOT go in the journal.

        These two were one function until the journal became something a user could
        read, at which point "everything we printed" turned out to be 69 lines of prose
        per recipe -- warnings, indented file lists, and the same bundle recorded twice
        under two different wordings. Provenance wants artifacts, not narration.
        """
        print(f"    {msg}")

    def record(self, artifact: str, msg: str | None = None) -> None:
        """Note a durable artifact in the journal, and optionally say something too."""
        if msg:
            print(f"    {msg}")
        if artifact not in self.changes:
            self.changes.append(artifact)

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
    ctx.record(step['dest'], f"installed {step['dest']} ({os.path.getsize(dest)} bytes)")


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
        ctx.record(spec['dest'], f"wrote {spec['dest']}")


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
    ctx.record("slax/boot/initrfs.img")


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


def _bundle_stack(ctx: "Ctx", want: list | None, below: str, verb: str) -> list[str]:
    """Resolve a `from:` list to bundle filenames, in the order the union will load them.

    THE DEFAULT IS THE WHOLE STACK, not 01-core. Building against a shorter stack makes
    apt reinstall libraries the image already has, and those fresh copies then shadow
    the originals from a higher bundle -- a version skew nobody asked for. `from:` is a
    size-versus-independence dial, not a correctness knob: name a shorter stack and you
    get a larger, self-contained bundle that survives its neighbours being removed.

    Two sharp edges fixed here. A prefix resolves to EVERY match, so `from: [01]` means
    01-core AND 01-firmware rather than silently just the first one; and the result is
    sorted the way sortmod sorts it, so `from: [03-desktop, 01-core]` no longer lets
    core win by being unpacked last.
    """
    mods = ctx.p("slax", "modules")
    have = [n for n in os.listdir(mods) if n.endswith(".sb")]
    if want:
        picked = []
        for w in want:
            hits = [n for n in have if n.startswith(w)]
            if not hits:
                raise RuntimeError(f"{verb}: no bundle matching {w!r} in slax/modules")
            picked += hits
    else:
        # 99-changes-N is a saved session, and 98-dpkg-db is generated at pack time from
        # the very fragments this build is about to produce. Neither belongs in a chroot.
        picked = [n for n in have
                  if not n.startswith("99-") and n != dpkgdb.GENERATED]
    # Deduplicate and order as the union will.
    picked = dpkgdb.sortmod(list(dict.fromkeys(picked)))
    # Nothing that loads at or above the bundle being built: it would not be underneath
    # it at boot, so a chroot containing it would be a lie about the finished image.
    # Naming one explicitly is always a mistake, so say so rather than dropping it --
    # silently building against the wrong base is how this whole class of bug works.
    high = [n for n in picked if n == below or dpkgdb.sortmod([n, below])[0] != n]
    if high and want:
        raise RuntimeError(
            f"{verb}: from: names {', '.join(high)}, which load at or above {below}. "
            f"A bundle can only be built against what will sit BELOW it at boot "
            f"(load order is the numeric prefix, and higher wins).")
    picked = [n for n in picked if n not in high]
    if not picked:
        raise RuntimeError(f"{verb}: no bundles to stack below {below}")
    return picked


# Maintainer scripts that need more of a running system than an unprivileged chroot
# can offer. _prepare_chroot creates /proc, /sys and /dev/pts as EMPTY DIRECTORIES --
# mounting them for real needs CAP_SYS_ADMIN, which a container usually does not have.
# dpkg then buries the cause under a hundred lines of its own output, so surface it.
CHROOT_LIMITS = [
    ("requires a mounted proc fs",
     "This package's postinst runs a program that needs a real /proc. The build chroot "
     "has an empty /proc directory, because mounting one needs CAP_SYS_ADMIN -- run "
     "`kitchen doctor` and look at the `mount` capability. Java is the common case: no "
     "JRE can be installed this way, so libreoffice-base and anything else needing one "
     "has to be left out or built on a privileged machine."),
    ("Is /dev/pts mounted?",
     "A maintainer script wanted a pty. The chroot has no /dev/pts, for the same reason "
     "as /proc above. This one is usually only a warning."),
]


def _chroot_hint(output: str) -> str:
    """Name the chroot limitation behind a maintainer-script failure, if it is one."""
    for needle, explanation in CHROOT_LIMITS:
        if needle in output:
            return f"\n  -> {explanation}\n\n"
    return ""


def _excluded(rel: str, extra: list | None = None) -> bool:
    """BUNDLE_EXCLUDE, plus anything this particular step asked to keep out.

    A third-party apt source and its signing key are the motivating case: they have to
    exist in the chroot for apt to use them, and shipping them would silently add that
    repository -- and that key's trust -- to the user's live system.
    """
    return bool(BUNDLE_EXCLUDE.search(rel)) or any(p.search(rel) for p in (extra or []))


def _apt_sources(ctx: "Ctx", root: str, apt: dict) -> list:
    """Add foreign architectures and third-party repositories to the build chroot.

    Returns exclusion patterns for anything that must not leave the chroot.

    Keys are pinned by sha256 like every other download here. An unpinned key is a
    remote party deciding what your image trusts, forever, and a bundle is exactly the
    artifact where that decision becomes permanent.
    """
    extra = []

    for arch in apt.get("architectures") or []:
        r = _in_chroot(root, ["dpkg", "--add-architecture", arch])
        if r.returncode != 0:
            raise RuntimeError(f"dpkg --add-architecture {arch}: "
                               + (r.stderr.strip() or r.stdout.strip())[-500:])
        ctx.say(f"added foreign architecture {arch}")

    for src in apt.get("sources") or []:
        name = src["name"]
        keyring = ""
        opts = []
        if src.get("key_url"):
            tmp = os.path.join(root, "tmp", f"{name}.key")
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with urllib.request.urlopen(src["key_url"], timeout=120) as r_, \
                    open(tmp, "wb") as f:
                shutil.copyfileobj(r_, f)
            got = sha256(tmp)
            if got != src["key_sha256"]:
                os.unlink(tmp)
                raise RuntimeError(
                    f"apt source {name}: signing key sha256 mismatch\n"
                    f"  want {src['key_sha256']}\n  got  {got}")
            # apt reads the FORMAT FROM THE EXTENSION under signed-by=: .asc must be
            # ASCII-armoured, .gpg must be binary. Naming an armoured key .gpg makes
            # apt report NO_PUBKEY for a key it is holding -- which reads exactly like
            # a wrong key, and cost an afternoon to see otherwise.
            with open(tmp, "rb") as f:
                armoured = f.read(40).lstrip().startswith(b"-----BEGIN PGP")
            keyring = (f"usr/share/keyrings/{name}-archive-keyring"
                       + (".asc" if armoured else ".gpg"))
            dest = os.path.join(root, keyring)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(tmp, dest)
            os.chmod(dest, 0o644)
            opts.append(f"signed-by=/{keyring}")
            ctx.say(f"apt source {name}: key pinned {got[:16]}... "
                    f"({'armoured' if armoured else 'binary'})")
        if src.get("architectures"):
            opts.append("arch=" + ",".join(src["architectures"]))
        line = ("deb " + (f"[{' '.join(opts)}] " if opts else "")
                + f"{src['uri']} {src['suite']} "
                + " ".join(src.get("components") or ["main"]) + "\n")
        listfile = f"etc/apt/sources.list.d/{name}.list"
        dest = os.path.join(root, listfile)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write(line)
        ctx.say(f"apt source {name}: {src['uri']} {src['suite']}")
        if src.get("keep"):
            ctx.say(f"  ships in the bundle -- the live system will trust {name} "
                    f"and can upgrade from it")
        else:
            extra.append(re.compile("^" + re.escape(listfile) + "$"))
            extra.append(re.compile("^" + re.escape(keyring) + "$"))
    return extra


def _read_status(root: str) -> str:
    """The chroot's dpkg database, or "" on a Slackware root that has none."""
    try:
        with open(os.path.join(root, dpkgdb.STATUS), encoding="utf-8",
                  errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _stage_delta(root: str, keep: list[str], stage: str) -> None:
    """Copy the delta out of a chroot, preserving what the chroot actually had.

    ONE COPY, not two. bundle.packages and bundle.script had this loop character for
    character, differing only in a temp variable -- so both bugs below lived in both
    places and either fix would have half-landed. lib/apply.py already has a comment
    about exactly this shape: "which is exactly how a rule ends up enforced by four verbs
    and not the fifth".

    Ownership is meaningful here and nowhere else in the bundle verbs: dpkg and the
    recipe's own script set it deliberately, inside a root chroot. Two ways it was lost:

    DIRECTORIES AND SYMLINKS had no chown at all. shutil.copystat copies "the permission
    bits, last access time, last modification time, and flags" -- "The file contents,
    owner, and group are unaffected". Measured against what users-and-auth leaves behind:

        in the chroot:  drwx------ 1100:1100  home/slaxuser
        in the bundle:  drwx------ root:root  <-- dir lost its owner
                        -rw-r--r-- 1100:1100  <-- its contents kept theirs

    mode 0700 on a root-owned directory is a hard deny, so the account that recipe exists
    to create could not enter its own home.

    SETUID AND SETGID were destroyed on files, which the old loop appeared to get right.
    The kernel clears S_ISUID/S_ISGID on chown -- even root chowning root to root, since
    Linux 2.2.13 -- so `copy2` then `chown` silently drops the bit:

        after shutil.copy2:  0o4755
        after os.chown(0,0): 0o755

    chromium-current installs chromium-sandbox, whose entire content is a setuid helper
    (-rwsr-xr-x root/root in the stock bundle). A recipe that exists to ship security
    fixes was shipping a Chromium whose sandbox could not work. chmod AFTER chown is the
    order tarfile itself uses, for this reason.
    """
    for rel in keep:
        src, dst = os.path.join(root, rel), os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        st = os.lstat(src)
        if os.path.islink(src):
            if not os.path.lexists(dst):
                os.symlink(os.readlink(src), dst)
                os.lchown(dst, st.st_uid, st.st_gid)
        elif os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            shutil.copystat(src, dst)
            os.chown(dst, st.st_uid, st.st_gid)
        else:
            shutil.copy2(src, dst)
            os.chown(dst, st.st_uid, st.st_gid)
            # AFTER the chown, which clears setuid/setgid. Not cosmetic: see the
            # docstring.
            os.chmod(dst, stat.S_IMODE(st.st_mode))


def _write_fragment(ctx: "Ctx", stage: str, name: str, before: str, after: str) -> None:
    """Record what this bundle added to the package database, as a mergeable fragment.

    Nothing is written when the database did not change -- a bundle.script that only
    runs useradd has no packages to declare.
    """
    if not after or after == before:
        return
    body = dpkgdb.delta(before, after)
    if not body.strip():
        return
    d = os.path.join(stage, dpkgdb.FRAGMENT_DIR)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name[:-3] if name.endswith(".sb") else name), "w") as f:
        f.write(body)
    ctx.say(f"dpkg fragment: {len(dpkgdb.parse(body))} package(s) declared "
            f"(merged into {dpkgdb.GENERATED} at pack time)")


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
        ctx.record("slax/boot/initrfs.img")
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
        ctx.record(f"slax/rootcopy{spec['dest']}", f"rootcopy: {spec['dest']}")


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
    ctx.record("slax/rootcopy/run/preinit.sh",
               f"slax/rootcopy/run/preinit.sh ({os.path.getsize(dest)} bytes) "
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

    Every verb that writes a RECIPE-named path goes through here. (Verbs that write
    ARCHIVE-named paths cannot use it -- see _refuse_escaping_link, which is the same
    guarantee for the one place where the paths come from a tarball instead.) Without it a
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


def _refuse_escaping_link(m, verb: str) -> None:
    """A tar member's NAME being safe says nothing about where its link POINTS.

    Checking only `m.name` leaves a whole class of escape open, because extraction
    follows a symlink that is already on disk. An archive holding a symlink `x -> /etc`
    and then a regular member `x/cron.d/kitchen` writes to the host through it -- and
    does so from a verb declaring `privilege: none`, which is exactly the set a reader
    trusts to be harmless. Measured before this existed: the verb printed "unpacked 2
    entries" and "built slax/modules/07-poc.sb", reported success, and left a file
    outside the work tree.

    THIS IS THE LEXICAL HALF ONLY, and it is not the same guarantee as _under. _under
    resolves with os.path.realpath, which FOLLOWS symlinks; this uses normpath, which does
    not -- so it cannot see a chain where an earlier member has already put a symlink on
    disk at a component it is collapsing. _extract_members does that half, at write time,
    and that is what closes the escape.

    This half still matters: it refuses an absolute link target, which _under deliberately
    does not (it lstrips the leading slash, correctly, for recipe-named paths). Without
    it a bundle can ship `x -> /etc`. Nothing can be written through that symlink, but a
    bundle has no business containing one.

    Not delegated to tarfile's `filter="data"`, which is the obvious fix and the wrong
    one here. That parameter landed in 3.11.4/3.12; this project's floor is python3 >= 3.9
    (containers/README.md) and Debian 12 -- one of our two container bases, and what
    upstream actually builds Slax on -- ships 3.11.2, where `extractall(filter=...)` is a
    TypeError and `tarfile.data_filter` does not exist. `filter="tar"` is available on the
    same versions and would NOT help: measured, it allows all three escapes below.

    Hardlinks count too. `data_filter` rejects them and the report that prompted this did
    not mention them, but LNKTYPE resolves against the extraction root just as SYMTYPE
    does.
    """
    if not (m.issym() or m.islnk()):
        return
    target = m.linkname
    if target.startswith("/"):
        raise RuntimeError(
            f"{verb}: archive member {m.name!r} is a link to an absolute path "
            f"({target!r}). Extraction would follow it out of the work tree.")
    # A symlink resolves relative to its OWN directory; a hardlink's target is relative
    # to the archive root. Both must stay inside the destination.
    base = os.path.dirname(m.name) if m.issym() else ""
    if os.path.normpath(os.path.join(base, target)).split("/")[0] == "..":
        raise RuntimeError(
            f"{verb}: archive member {m.name!r} is a link to {target!r}, which resolves "
            f"outside the tree being unpacked. '..' is refused in a link target exactly "
            f"as it is in a member name.")


# THE ONLY TWO NUMBERS THAT ARE NOT MERELY ORDERING.
#
# The rest of the scheme -- 00-09 platform, 10-89 yours, 90-97 headroom -- is convention.
# A fork that ignores it gets a different load order and nothing else, which is its
# business. These two are different, and both were measured rather than assumed:
#
#   98  `kitchen pack` DELETES and rewrites 98-dpkg-db.sb every time (dpkgdb.merge_tree).
#       It has to: a generated bundle left in place becomes the base on the next pack and
#       freezes the database. So a recipe's own 98-dpkg-db.sb is destroyed with no error,
#       and a 98-anything-else silently outranks the package database.
#
#   99  upstream's savechanges picks the next session number by
#           ls -1 | sort -V | tail -n 1 | sed 's/^99-changes-//; s/[.]sb$//'   then + 1
#       which assumes the last file in slax/modules/ is a 99-changes-N.sb. With a bundle
#       named 99-mystuff.sb present it computes 100 on EVERY boot -- 99-mystuff.sb sorts
#       after 99-changes-100.sb -- so each saved session overwrites the previous one. With
#       a dot in the stem (99-my.tools.sb) the arithmetic is a bash syntax error and the
#       target becomes 99-changes-.sb. Either way the user loses saved work.
#
# 97 does everything 98 would and collides with nothing.
RESERVED_PREFIXES = {
    "98": "kitchen pack generates 98-dpkg-db.sb and rewrites it on every pack",
    "99": "savechanges writes 99-changes-N.sb and derives N from the last file here",
}


def _check_reserved(num: str, name: str, verb: str) -> None:
    """Refuse a reserved numeric prefix. See RESERVED_PREFIXES for the measurements."""
    if num in RESERVED_PREFIXES:
        raise RuntimeError(
            f"{verb}: {num} is reserved -- {RESERVED_PREFIXES[num]}. Use 97 to sit "
            f"above every other bundle; got {name!r}")


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
    _check_reserved(name[:2], name, verb)
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
    ctx.record(f"slax/modules/{name}")
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
                # AFTER the strip, not with the name check above. Stripping changes the
                # member's depth, and a relative link target is resolved from wherever the
                # member ends up: `a/b/c -> ../../etc` stays inside at depth 3 and escapes
                # at depth 1 once `strip: 1` has rewritten the name.
                _refuse_escaping_link(m, "bundle.fromTarball")
                _refuse_privileged_member(m, "bundle.fromTarball")
                members.append(m)
            if not members:
                raise RuntimeError(f"bundle.fromTarball: nothing left after strip: {strip}")
            # Pin the filter rather than inheriting a default that changes under us.
            # 3.12 warns that 3.14 will switch the default to "data", which refuses device
            # nodes and strips setuid/setgid -- a silent change to what a bundle CONTAINS,
            # on some interpreters and not others. `data` would also refuse the chain
            # _extract_members guards against, and it is still unusable here: it needs
            # 3.11.4+ and debian:12 ships 3.11.2 with no filter machinery at all.
            kw = {"filter": "fully_trusted"} if hasattr(tarfile, "fully_trusted_filter") else {}
            _extract_members(t, dest, members, kw)
        ctx.say(f"unpacked {len(members)} entries from {os.path.basename(src)}"
                + (f" under /{prefix}" if prefix else ""))
        _make_bundle(ctx, root, name, "bundle.fromTarball")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _refuse_privileged_member(m, verb: str) -> None:
    """Refuse what a `privilege: none` verb has no business producing.

    This verb declares privilege: none and VERB_REQUIRES asks only for mksquashfs, so a
    reader of a recipe using it treats it as harmless. Extraction runs as root in
    practice -- apply.py is run under sudo for a whole plan whenever any recipe in it
    needs a chroot -- and tarfile's fully_trusted extraction faithfully applies whatever
    the archive asks for. So without this an unpinned tarball could put a setuid-root
    binary in /usr/bin and a device node in /dev, in a bundle that is mounted into the
    union on every boot. sha256: is optional on this verb; absent, it only warns.

    Refused rather than stripped. Stripping is quieter and would produce a bundle that
    silently does less than the archive said -- and this project would rather stop than
    guess. bundle.script under privilege: chroot is the honest route for content that
    genuinely needs either, which is what bundle-from-txz already does deliberately.

    Not delegated to filter="data", which refuses both: it needs 3.11.4+ and debian:12
    ships 3.11.2. Same floor argument as _extract_members.

    Ordering note: this must land BEFORE -all-root is turned on for this verb.
    -all-root rewrites ids and leaves mode bits alone, so it converts a surviving
    "setuid nobody" into "setuid root" -- measured. Without this check, that flag makes
    the problem worse rather than better.
    """
    if m.isdev() or m.isfifo():
        kind = "device node" if m.isdev() else "FIFO"
        raise RuntimeError(
            f"{verb}: archive member {m.name!r} is a {kind}. This verb is "
            f"privilege: none and its output is mounted as root at boot. Use "
            f"bundle.script (privilege: chroot) if the bundle genuinely needs one.")
    if m.mode & (stat.S_ISUID | stat.S_ISGID):
        which = "setuid" if m.mode & stat.S_ISUID else "setgid"
        raise RuntimeError(
            f"{verb}: archive member {m.name!r} is {which} (mode {m.mode:04o}). An "
            f"archive this verb downloads must not decide what runs privileged on the "
            f"built image -- sha256: is optional here, so the publisher of the URL "
            f"would be deciding. Use bundle.script (privilege: chroot) if it is "
            f"genuinely needed.")


def _extract_members(t, dest: str, members: list, kw: dict) -> None:
    """Extract one member at a time, refusing any that resolves outside `dest`.

    THE LEXICAL CHECK CANNOT DO THIS. _refuse_escaping_link reads each member's link
    target with os.path.normpath, which collapses `..` textually -- it has no way to know
    an EARLIER member already placed a symlink on disk at one of the components it is
    collapsing. GHSA-p2w2-qh4r-jr53 was published claiming 804725c fixed it; it did not,
    and this is the miss. Three members, each individually clean:

        'a/d'            -> symlink '..'        normpath("a/..")   == "."  accepted
        'e'              -> symlink 'a/d/..'    normpath("a/d/..") == "a"  accepted
        'e/OUTSIDE/pwned'   regular file        name is clean              accepted

    and the kernel resolves `e` through the on-disk `a/d` and lands above dest. Measured
    before this existed: "wrote OUTSIDE dest? True", "files left inside dest: 0" -- the
    payload left the tree entirely, from a verb declaring privilege: none.

    WHAT EACH HALF IS FOR. Measured, not assumed -- an earlier draft of this comment
    claimed deleting the lexical pass would reopen GHSA-p2w2-qh4r-jr53, and that is
    false: with the lexical pass removed, the original PoC is still refused here, at the
    payload member ("archive member 'x/cron.d/kitchen' resolves outside"). This check
    alone closes the escape.

    The lexical pass earns its place for a different reason. _under is blind to absolute
    link TARGETS by design -- it does rel.lstrip("/"), because for a recipe-named `dest:`
    "/etc" properly means "etc, under the tree" -- so `x -> /etc` passes here and the
    symlink gets created. Nothing can then be written through it, but the bundle ships a
    symlink pointing at an absolute host path, which is not something a bundle should
    contain. _refuse_escaping_link refuses that outright, before anything is extracted.

      _refuse_escaping_link  absolute targets, single-member ..   fails fast, no writes
      _under (here)          chains, symlinked parents           closes the escape

    Member by member, because the check has to run BEFORE each write: after a bulk
    extractall the file is already outside. The directory handling mirrors CPython's
    extractall, which defers directory attributes "since permissions can interfere with
    extraction and extracting contents can reset mtime" -- without that, every bundle
    built from a tarball would carry wrong directory modes and mtimes. Verified
    byte-for-byte identical to extractall on an archive with a 0700 dir, a sticky 1777
    dir and pinned mtimes.
    """
    dest = os.path.realpath(dest)
    directories = []
    for m in members:
        # Where it will actually land, following any symlink already extracted.
        _under(dest, m.name, "bundle.fromTarball", "archive member")
        if m.issym() or m.islnk():
            # A symlink resolves from its own directory; a hardlink from the root.
            base = os.path.dirname(m.name) if m.issym() else ""
            _under(dest, os.path.join(base, m.linkname),
                   "bundle.fromTarball", f"link target of {m.name!r}")
        if m.isdir():
            directories.append(m)
            saved, m.mode = m.mode, 0o700
            t.extract(m, dest, **kw)
            m.mode = saved
        else:
            t.extract(m, dest, **kw)
    for m in sorted(directories, key=lambda a: a.name, reverse=True):
        p = os.path.join(dest, m.name)
        os.chmod(p, m.mode)
        os.utime(p, (m.mtime, m.mtime))


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
    _check_reserved(to, f"{to}-", "bundle.renumber")
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
        ctx.record(f"slax/modules/{new}")


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

    The chroot inherits whatever network the host has -- there is no isolation here, and
    there cannot be without a user namespace. Declare `network: true` on the step when
    the script fetches something, so a recipe that depends on the internet says so in
    the YAML and preflight fails fast instead of unsquashing 122 MiB first.
    """
    script = step.get("script")
    if not script:
        raise RuntimeError("bundle.script: no script given")
    name = _bundle_name(step["bundle"], "bundle.script")
    stack = _bundle_stack(ctx, step.get("from"), name, "bundle.script")
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
        mods = ctx.p("slax", "modules")
        for n in stack:
            r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", root,
                                os.path.join(mods, n)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"unsquashfs {n}: {r.stderr.strip()[:300]}")
        ctx.say(f"unpacked {' + '.join(stack)} as the build root")
        _prepare_chroot(root)
        # BEFORE the snapshot below, which is what keeps the delta honest: before_status
        # becomes the merged view, so after-minus-before still isolates only what THIS
        # bundle installed. Merging after the snapshot would re-declare everything the
        # merge added. See dpkgdb.merge_fragments_into_chroot for why it is needed.
        _n, _from = dpkgdb.merge_fragments_into_chroot(root)
        if _n:
            ctx.say(f"merged {_n} status fragment(s) into the build chroot "
                    f"({', '.join(_from)})")

        before, before_status = _manifest(root), _read_status(root)
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
        after, after_status = _manifest(root), _read_status(root)

        added = [k for k in after if k not in before]
        modified = [k for k in after if k in before and after[k] != before[k]]
        keep = sorted(k for k in added + modified if not BUNDLE_EXCLUDE.search(k))
        ctx.say(f"delta: {len(added)} added, {len(modified)} modified, "
                f"{len(keep)} kept after exclusions")
        if not keep:
            raise RuntimeError("bundle.script: the script changed nothing that survives "
                               "the exclusion list; nothing to package")

        stage = os.path.join(build, "stage")
        _stage_delta(root, keep, stage)
        _write_fragment(ctx, stage, name, before_status, after_status)
        _make_bundle(ctx, stage, name, "bundle.script")
    finally:
        shutil.rmtree(build, ignore_errors=True)


@verb("bundle.remove")
def v_bundle_remove(ctx: Ctx, step: dict) -> None:
    """Delete bundles matching a regex. Must run before anything is built on top.

    Removing a bundle is not just dropping files: every bundle above it was built with
    that one in its `from:` stack, so apt saw its libraries as already installed and did
    not ship copies. Delete it afterwards and those binaries have an unresolvable NEEDED
    -- silently, because the file delta is empty for the missing libraries and the merged
    package database stays self-consistent. check_plan_order refuses that ordering before
    anything runs; this docstring is where the rule is stated.
    """
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
        ctx.record(f"-slax/modules/{n}", f"removed {n} (-{size // 1048576} MiB)")


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
    # silently ignored while reporting "cmdline updated on 2 entries".
    #
    # Two things have changed since that was written, and the comment used to claim this
    # was "the only place to catch it". 42106db closed the step schema with
    # unevaluatedProperties, so `add:` is now a validation error; and 45-doc-yaml
    # validates the examples in docs/ too, which is what found the three pages still
    # showing `add:` long after the verb reference had been corrected. This check stays
    # as the backstop for a caller that bypasses validation.
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
        ctx.record(f"slax/boot/{name}",
                   f"{name}: cmdline updated on {touched} entr{'y' if touched == 1 else 'ies'}")


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
    # `sign: true` validates and can never work: it reaches pack.sh as the gpg key id
    # "True". The schema cannot express "a string, or the boolean false but not true",
    # so the verb says it. `false` stays legal and is already a no-op below.
    if step.get("sign") is True:
        raise RuntimeError("iso.checksums: sign takes a gpg key id, not a boolean -- "
                           "e.g. sign: \"ABCD1234EF\". Omit it, or set false, for no "
                           "signature.")
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
    ctx.record(dest_rel)


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
    # Debian's package database is ONE FILE, and a union composes trees, not files. A
    # bundle shipping its own copy replaces the one below it wholesale -- our own
    # add-packages did exactly that, putting a 299-package status above 05-chromium's
    # 600 and making dpkg forget three hundred packages, silently. Ship a fragment in
    # var/lib/slax-kitchen/dpkg-status.d/ instead; pack merges them. See lib/dpkgdb.py.
    # (var/lib/dpkg/info/ is a DIRECTORY of per-package files and unions correctly, so
    # it stays.)
    r"|^var/lib/dpkg/(status|status-old|available|available-old)$"
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
    # aufs whiteouts, at ANY depth -- this was anchored to the tree root, so it caught
    # .wh.foo and missed etc/.wh.foo, which is where one would actually appear. A
    # whiteout packed into a bundle DELETES that file for everyone who loads the
    # bundle; an offline package install has no legitimate reason to produce one, and
    # an accidental one would silently remove something the image shipped.
    r"|(^|/)\.wh\."
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
    # _bundle_name rather than a second copy of the same check: this verb used to
    # re-implement it inline, which is exactly how a rule ends up enforced by four verbs
    # and not the fifth.
    out_name = _bundle_name(step.get("bundle") or "07-packages", "bundle.packages")

    mods = ctx.p("slax", "modules")
    stack = _bundle_stack(ctx, step.get("from"), out_name, "bundle.packages")
    flavour = step.get("flavour") or _detect_flavour(ctx.tree)

    if ctx.dry:
        ctx.say(f"would install {', '.join(packages)} into {out_name} "
                f"(flavour={flavour}, stack={'+'.join(stack)})")
        return

    import tempfile
    build = tempfile.mkdtemp(prefix="kitchen-pkg-", dir=os.path.dirname(os.path.abspath(ctx.work)))
    root = os.path.join(build, "root")
    try:
        # 1. Stack the source bundles in load order so later ones win, exactly as the
        #    union does at boot. _bundle_stack already sorted them; unsquashfs -f then
        #    overwrites, which is how "higher wins" is emulated here.
        os.makedirs(root, exist_ok=True)
        for n in stack:
            r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", root,
                                os.path.join(mods, n)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"unsquashfs {n}: {r.stderr.strip()[:300]}")
        ctx.say(f"unpacked {' + '.join(stack)} as the build root")

        # 2. Make it a usable root filesystem (see RUNTIME_DIRS).
        _prepare_chroot(root)
        # BEFORE the snapshot below, which is what keeps the delta honest: before_status
        # becomes the merged view, so after-minus-before still isolates only what THIS
        # bundle installed. Merging after the snapshot would re-declare everything the
        # merge added. See dpkgdb.merge_fragments_into_chroot for why it is needed.
        _n, _from = dpkgdb.merge_fragments_into_chroot(root)
        if _n:
            ctx.say(f"merged {_n} status fragment(s) into the build chroot "
                    f"({', '.join(_from)})")

        # 3. Snapshot, install, snapshot.
        before, before_status = _manifest(root), _read_status(root)
        step_excludes: list = []
        if flavour == "debian":
            with open(os.path.join(root, "etc/apt/apt.conf.d/00kitchen"), "w") as f:
                f.write('APT::Sandbox::User "root";\nAcquire::Retries "3";\n')
            # Foreign architectures and third-party repos must be in place BEFORE the
            # index refresh, or apt-get update will not see them.
            step_excludes = _apt_sources(ctx, root, step.get("apt") or {})
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
            out = (r.stderr.strip() or r.stdout.strip())
            raise RuntimeError(f"package install failed (exit {r.returncode}):\n"
                               + _chroot_hint(out) + out[-1500:])

        # Never trust the exit code alone -- see _installed().
        missing = [p for p in packages if not _installed(root, flavour, p)]
        if missing:
            tail = (r.stdout.strip() or r.stderr.strip())[-1200:]
            raise RuntimeError(
                f"package manager reported success but these are not installed: "
                f"{', '.join(missing)}\n--- last output ---\n{tail}")
        ctx.say(f"verified installed: {', '.join(packages)}")
        after, after_status = _manifest(root), _read_status(root)

        # 4. Delta = added OR modified. Names alone are not enough.
        added = [k for k in after if k not in before]
        modified = [k for k in after if k in before and after[k] != before[k]]
        keep = sorted(k for k in added + modified if not _excluded(k, step_excludes))
        ctx.say(f"delta: {len(added)} added, {len(modified)} modified, "
                f"{len(keep)} kept after exclusions")
        if not keep:
            raise RuntimeError("bundle.packages: nothing to package "
                               "(were the packages already present in the base?)")

        # 5. Stage just the delta, preserving parent directory metadata.
        stage = os.path.join(build, "stage")
        _stage_delta(root, keep, stage)

        # 6. Declare what was added to the package database, without shipping the
        #    database itself. BUNDLE_EXCLUDE drops var/lib/dpkg/status; this replaces it.
        _write_fragment(ctx, stage, out_name, before_status, after_status)

        # 7. Build with upstream's exact parameters (livekitlib create_bundle / dir2sb).
        _make_bundle(ctx, stage, out_name, "bundle.packages")
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


def load_recipe(path: str, overrides: dict | None = None) -> dict:
    import yaml
    problems = validate_file(path, overrides)
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
        # Collect EVERY match, not the first. A name that exists in two recipe
        # directories is ambiguous, and silently taking whichever sorted first is the
        # same bug _find_bundle had: the caller gets something plausible and wrong.
        hits = [os.path.join(d, n + ext)
                for d in search for ext in (".yaml", ".yml")
                if os.path.isfile(os.path.join(d, n + ext))]
        if not hits:
            raise RuntimeError(f"recipe not found: {n} (searched {', '.join(search)})")
        if len(hits) > 1:
            raise RuntimeError(
                f"recipe name {n!r} is ambiguous -- it exists in more than one place:\n  "
                + "\n  ".join(os.path.relpath(h, ROOT) for h in hits)
                + "\n  Rename one, or name the file you mean by path.")
        return hits[0]

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


def plan_recipe(path: str, facts: dict,
                overrides: dict | None = None) -> tuple[dict, list[tuple[int, dict, bool]]]:
    """Resolve a recipe into (doc, [(index, step, will_run)]).

    Shared by preflight and apply so they cannot disagree about which steps run --
    demanding a tool for a step that `when:` is going to skip would be its own bug.

    `overrides` come from a profile's per-recipe `vars:`. They are merged OVER the
    recipe's own defaults, never replacing the block, so a profile setting one var does
    not silently blank the rest -- which is the trap `--facts` fell into.
    """
    doc = load_recipe(path, overrides)
    vars_ = dict(doc.get("vars", {}) or {})
    if overrides:
        vars_.update(overrides)
    steps = []
    for i, raw in enumerate(doc["steps"], 1):
        step = subst(raw, vars_)
        run = "when" not in step or _when_ok(step["when"], facts)
        steps.append((i, step, run))
    return doc, steps


def _journal_entry(work: str, recipe: str) -> dict | None:
    """The journal entry for `recipe` in this tree, if it has been applied."""
    jpath = os.path.join(work, ".kitchen", "journal.yaml")
    if not os.path.isfile(jpath):
        return None
    try:
        import yaml
        j = yaml.safe_load(open(jpath)) or {}
    except Exception:
        return None
    for entry in reversed(j.get("applied") or []):
        if entry.get("recipe") == recipe:
            return entry
    return None


def apply_recipe(path: str, work: str, dry: bool = False,
                 overrides: dict | None = None) -> int:
    facts = _tree_facts(work, os.path.join(work, "iso"))
    doc, steps = plan_recipe(path, facts, overrides)
    name = doc["metadata"]["name"]
    ctx = Ctx(work, os.path.dirname(os.path.abspath(path)), name, dry)
    ctx.facts = facts
    print(f"  {name}: {doc['metadata']['summary']}")
    for w in check_compat(doc, work):
        print(f"    warning: {w}", file=sys.stderr)

    # The journal already knows this recipe ran. Saying so beats letting the user
    # discover it from whichever verb happens to collide first -- `bundle.files` used to
    # report "slax/modules/07-branding.sb already exists. Pick another number", which is
    # true, unhelpful, and points at the wrong problem.
    prior = _journal_entry(work, name)
    if prior and not dry:
        raise RuntimeError(
            f"{name} was already applied to this tree at {prior.get('at', 'an earlier time')}.\n"
            f"  It produced: {', '.join(prior.get('artifacts') or ['(nothing recorded)'])}\n"
            f"  Recipes are not idempotent -- applying one twice is a mistake, not a no-op.\n"
            f"  See `kitchen status {work}`; to start over, unpack the base ISO again.")

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
            if v in NOT_YET:
                raise RuntimeError(f"step {i}: verb '{v}' is not implemented yet -- "
                                   f"{NOT_YET[v]}")
            raise RuntimeError(f"step {i}: verb '{v}' is valid in the schema but not "
                               f"implemented yet (have: {', '.join(sorted(VERBS))})")
        fn(ctx, step)

    if not dry:
        import yaml
        jpath = os.path.join(ctx.meta, "journal.yaml")
        j = (yaml.safe_load(open(jpath)) if os.path.isfile(jpath) else None) or {"applied": []}
        import datetime
        entry = {
            "recipe": name,
            "at": datetime.datetime.now(datetime.timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%SZ"),
            # The verbs that actually ran, so a `when:`-skipped step does not appear as
            # something this tree had done to it.
            "verbs": [st["verb"] for _i, st, run in steps if run],
            "artifacts": ctx.changes,
        }
        # Without this, two applications of serial-console with different ports are
        # indistinguishable on disk. The journal exists so a `kitchen probe` difference
        # can be traced back to a recipe; a var that changed the output is part of that.
        if overrides:
            entry["vars"] = dict(overrides)
        j["applied"].append(entry)
        with open(jpath, "w") as f:
            yaml.safe_dump(j, f, sort_keys=False)
    return 0


def recipe_search_path() -> list[str]:
    """Every directory under recipes/, in order, so a fork can just make one.

    `recipes/available/` is this repo's library. A fork keeping its own recipes in
    `recipes/<project>/` gets them resolvable by bare name with no configuration: drop
    the folder in, and `kitchen apply my-tools` finds it. 40-schema validates everything
    under recipes/; 90-doc-coverage polices only available/, so a fork owes the upstream
    cookbook nothing.

    available/ comes first so this repo's own names win a tie predictably -- but a tie is
    reported rather than resolved, see resolve().
    """
    base = os.path.join(ROOT, "recipes")
    if not os.path.isdir(base):
        return []
    dirs = sorted(d for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)) and not d.endswith(".files"))
    first = [d for d in dirs if d == "available"]
    return [os.path.join(base, d) for d in first + [d for d in dirs if d != "available"]]


def read_profile_recipes(path: str) -> tuple[list[str], dict[str, dict]]:
    """Recipe names and per-recipe var overrides from a profile.

    A profile is the authoritative place for a fork's own values -- a bundle number, a
    serial port -- because it is committed next to the recipes it configures, and
    `kitchen build <profile>` reproduces it. The cookbook has documented this syntax
    since before it worked.
    """
    import yaml
    if not os.path.isfile(path):
        cand = os.path.join(ROOT, "profiles", path + ".yaml")
        if os.path.isfile(cand):
            path = cand
        else:
            raise RuntimeError(f"profile not found: {path}")
    problems = validate_file(path)
    if problems:
        raise RuntimeError(f"invalid profile {path}:\n  " + "\n  ".join(problems))
    doc = yaml.safe_load(open(path))
    names: list[str] = []
    overrides: dict[str, dict] = {}
    for entry in doc.get("recipes") or []:
        if isinstance(entry, str):
            names.append(entry)
            continue
        names.append(entry["name"])
        if entry.get("vars"):
            overrides[entry["name"]] = dict(entry["vars"])
    return names, overrides


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kitchen apply",
                                 description="apply recipes to a work tree")
    ap.add_argument("recipes", nargs="*")
    ap.add_argument("--profile", metavar="PATH",
                    help="take the recipe list AND its per-recipe vars from a profile "
                         "instead of naming recipes here")
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
    # Argument errors first. "no work tree" is a confusing answer to "you named no
    # recipes", and checking the tree earlier hid the mutually-exclusive case entirely.
    if a.profile and a.recipes:
        print("error: --profile and naming recipes are mutually exclusive "
              "(the profile already carries the list)", file=sys.stderr)
        return 2
    if not a.profile and not a.recipes:
        print("error: name at least one recipe, or pass --profile PATH", file=sys.stderr)
        return 2
    var_overrides: dict[str, dict] = {}
    names = a.recipes
    if a.profile:
        try:
            names, var_overrides = read_profile_recipes(a.profile)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if not names:
            print(f"error: {a.profile} lists no recipes", file=sys.stderr)
            return 2
    if not a.preflight_only and not os.path.isdir(os.path.join(a.work, "iso")):
        print(f"no work tree at {a.work}/iso (run 'kitchen unpack' first)", file=sys.stderr)
        return 2
    search = recipe_search_path() + [os.getcwd()]
    try:
        paths = resolve(names, search)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Overrides are keyed by recipe name, and validate_file guarantees the name matches
    # the filename stem -- so a recipe pulled in by compat.requires, which the profile
    # never named, correctly gets none.
    def ov(path: str) -> dict | None:
        return var_overrides.get(os.path.splitext(os.path.basename(path))[0])
    if a.preflight_only:
        print(f"preflight {len(paths)} recipe(s)"
              + (f"  [{a.facts}]" if a.facts else ""))
    else:
        print(f"apply {len(paths)} recipe(s) to {a.work}"
              + ("  [dry run]" if a.dry_run else ""))

    # Check everything the WHOLE plan needs before touching anything. Without this the
    # first three recipes apply, download files and edit configs, and the fourth dies on
    # a missing tool -- leaving a half-modified tree.
    # The plan is built unconditionally. --skip-preflight is documented as "do not check
    # tools/capabilities first" -- it is about this MACHINE, and it used to switch off
    # the ordering rule too, because the rule lived inside its guard. Nothing said so,
    # and composing-bundles.md says flatly "This is enforced".
    facts = dict(override) if override else _tree_facts(a.work, os.path.join(a.work, "iso"))
    plan: list[tuple[str, dict]] = []
    for p in paths:
        try:
            doc, steps = plan_recipe(p, facts, ov(p))
        except RuntimeError as e:
            print(f"error: {os.path.basename(p)}: {e}", file=sys.stderr)
            return 2
        for _i, step, run in steps:
            if run:
                plan.append((doc["metadata"]["name"], step))

    # Ordering is a property of the PLAN, not of this machine, so it gets its own
    # message rather than being folded into preflight's "unmet requirement(s)".
    #
    # The work tree goes in whenever there is one. Under --preflight-only there is not
    # -- `kitchen build` preflights before it unpacks -- and the check falls back to
    # plan-only, which is exactly its old behaviour.
    order = check_plan_order(plan, None if a.preflight_only else a.work)
    if order:
        sys.stdout.flush()
        print(f"\nplan rejected -- {len(order)} ordering problem(s), nothing has "
              "been modified:", file=sys.stderr)
        for pr in order:
            print(f"  - {pr}", file=sys.stderr)
        print("\nSee docs/40-workflow/composing-bundles.md.", file=sys.stderr)
        sys.stderr.flush()
        return 2

    if not a.skip_preflight:
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
            apply_recipe(p, a.work, a.dry_run, ov(p))
        except Exception as e:                       # noqa: BLE001
            print(f"error: {os.path.basename(p)}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
