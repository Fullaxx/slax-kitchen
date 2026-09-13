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
import shutil
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import validate_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERBS: dict = {}


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

@verb("boot.payload")
def v_boot_payload(ctx: Ctx, step: dict) -> None:
    """Put a file into slax/boot/ -- a memtest binary, a .c32 module, an EFI blob."""
    dest = ctx.p(step["dest"])
    src = step.get("src")
    want = step.get("sha256")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if ctx.dry:
        ctx.say(f"would install {step['dest']} from {src}")
        return
    if src and re.match(r"^https?://", src):
        tmp = dest + ".part"
        with urllib.request.urlopen(src, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        got = sha256(tmp)
        if want and got != want:
            os.unlink(tmp)
            raise RuntimeError(f"{step['dest']}: sha256 mismatch\n  want {want}\n  got  {got}")
        os.replace(tmp, dest)
    elif src:
        local = src if os.path.isabs(src) else os.path.join(ctx.recipe_dir, src)
        if not os.path.isfile(local):
            raise RuntimeError(f"boot.payload: source not found: {local}")
        got = sha256(local)
        if want and got != want:
            raise RuntimeError(f"{step['dest']}: sha256 mismatch\n  want {want}\n  got  {got}")
        shutil.copy2(local, dest)
    if "mode" in step:
        os.chmod(dest, int(step["mode"], 8))
    ctx.say(f"installed {step['dest']} ({os.path.getsize(dest)} bytes)")


@verb("iso.files")
def v_iso_files(ctx: Ctx, step: dict) -> None:
    for spec in step["files"]:
        dest = ctx.p(spec["dest"])
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


@verb("rootcopy.files")
def v_rootcopy_files(ctx: Ctx, step: dict) -> None:
    """Drop files into /slax/rootcopy/, which livekit copies onto the union at boot.

    No bundle rebuild, no squashfs work -- the cheapest customization there is.
    Upstream ships no rootcopy directory at all, so this creates it.
    """
    for spec in step["files"]:
        dest = ctx.p("slax", "rootcopy", spec["dest"].lstrip("/"))
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
    """Add or remove kernel parameters on APPEND lines."""
    add = step.get("append", [])
    drop = step.get("remove", [])
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
            lines[i] = "APPEND " + " ".join(parts)
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


# ------------------------------------------------------------- engine --------

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


def apply_recipe(path: str, work: str, dry: bool = False) -> int:
    doc = load_recipe(path)
    name = doc["metadata"]["name"]
    ctx = Ctx(work, os.path.dirname(os.path.abspath(path)), name, dry)
    print(f"  {name}: {doc['metadata']['summary']}")
    for w in check_compat(doc, work):
        print(f"    warning: {w}", file=sys.stderr)

    vars_ = dict(doc.get("vars", {}) or {})
    for i, raw in enumerate(doc["steps"], 1):
        step = subst(raw, vars_)
        v = step["verb"]
        fn = VERBS.get(v)
        if fn is None:
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
    a = ap.parse_args(argv[1:])
    if not os.path.isdir(os.path.join(a.work, "iso")):
        print(f"no work tree at {a.work}/iso (run 'kitchen unpack' first)", file=sys.stderr)
        return 2
    search = [os.path.join(ROOT, "recipes", "available"),
              os.path.join(ROOT, "recipes", "examples"), os.getcwd()]
    try:
        paths = resolve(a.recipes, search)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"apply {len(paths)} recipe(s) to {a.work}" + ("  [dry run]" if a.dry_run else ""))
    for p in paths:
        try:
            apply_recipe(p, a.work, a.dry_run)
        except Exception as e:                       # noqa: BLE001
            print(f"error: {os.path.basename(p)}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
