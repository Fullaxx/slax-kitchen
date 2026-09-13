#!/usr/bin/env python3
"""Match an ISO against the known-release fingerprints in compat/.

Answers two different questions with one mechanism:

  "what is this?"        -- an unmodified ISO should match a compat/ entry exactly.
  "what did I change?"   -- a customized ISO should match one closely, and the diff
                            list is a readable summary of what the recipes did.

Differences are classified rather than just counted, because most of them are
expected. A rebuild always changes iso.sha256; the uefi-bootable recipe is *supposed*
to add an EFI El Torito entry. Only unexplained changes should worry you.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fingerprint import fingerprint  # noqa: E402

# Fields that legitimately change on any rebuild, even with no recipes applied.
BENIGN = {
    "iso.sha256":            "any rebuild changes this",
    "iso.size":              "padding/layout differs between mastering tools",
    "iso.volume_space":      "padding/layout differs between mastering tools",
    "iso.application_id":    "xorriso uppercases it; genisoimage preserves case",
    "eltorito.boot_info_table.file_len": "recomputed at mastering time",
}
# Suffix-matched benign fields (the prefix varies, e.g. per bundle name).
BENIGN_SUFFIX = {
    ".offset": "byte offset inside the ISO is file placement, not identity -- "
               "different mastering tools lay files out differently",
}


def benign_reason(field: str) -> str | None:
    if field in BENIGN:
        return BENIGN[field]
    for suf, why in BENIGN_SUFFIX.items():
        if field.endswith(suf):
            return why
    return None
# Differences these recipes are meant to cause. Used to explain, not to excuse.
EXPLAINS = [
    ("eltorito.uefi_bootable",  False, True,  "uefi-bootable"),
    ("iso.isohybrid_mbr",       False, True,  "isohybrid"),
    ("iso.gpt",                 False, True,  "isohybrid"),
]
# Changing any of these means the ISO is not the release it claims to be.
CRITICAL_PREFIXES = ("kernel.release", "initramfs.scripts", "metadata.flavour",
                     "metadata.arch", "identity.arch")


def flatten(obj, prefix="") -> dict:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        out[prefix] = f"[{len(obj)} items]" if any(
            isinstance(x, (dict, list)) for x in obj) else obj
    else:
        out[prefix] = obj
    return out


def compare(have: dict, want: dict) -> list[tuple[str, object, object]]:
    a, b = flatten(have), flatten(want)
    diffs = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            diffs.append((k, b.get(k), a.get(k)))     # (field, expected, actual)
    return collapse_bundles(diffs)


def collapse_bundles(diffs: list) -> list:
    """Report a whole added/removed bundle as one line, not one per superblock field.

    Dropping 05-chromium.sb is a single intentional act (the remove-bundle recipe);
    printing nine "expected ..., got None" lines for it buries the signal.
    """
    by_bundle: dict[str, list] = {}
    rest = []
    for d in diffs:
        parts = d[0].split(".")
        if len(parts) >= 3 and parts[0] == "bundles":
            by_bundle.setdefault(".".join(parts[1:-1]), []).append(d)
        else:
            rest.append(d)
    out = list(rest)
    for name, group in by_bundle.items():
        if all(act is None for _k, _e, act in group):
            out.append((f"bundles.{name}", "present", "REMOVED"))
        elif all(exp is None for _k, exp, _a in group):
            out.append((f"bundles.{name}", "absent", "ADDED"))
        else:
            out.extend(group)
    return sorted(out, key=lambda d: d[0])


def score(diffs: list) -> int:
    """Lower is better. Benign differences cost nothing."""
    return sum(0 if benign_reason(k) else 1 for k, _e, _a in diffs)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="match an ISO against compat/ fingerprints")
    ap.add_argument("iso")
    ap.add_argument("--compat-dir", default=None)
    ap.add_argument("-v", "--verbose", action="store_true", help="list every difference")
    a = ap.parse_args(argv[1:])
    if not os.path.isfile(a.iso):
        print(f"no such file: {a.iso}", file=sys.stderr)
        return 2
    try:
        import yaml
    except ImportError:
        print("PyYAML required", file=sys.stderr)
        return 2

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cdir = a.compat_dir or os.path.join(root, "compat")
    known = sorted(glob.glob(os.path.join(cdir, "*.yaml")))
    if not known:
        print(f"no fingerprints in {cdir}", file=sys.stderr)
        return 2

    fp = fingerprint(a.iso)
    results = []
    for path in known:
        ref = yaml.safe_load(open(path))
        # boot_files and bundles are compared structurally, not file-by-file, so a
        # single changed bundle does not drown out everything else.
        d = compare({k: v for k, v in fp.items() if k != "boot_files"},
                    {k: v for k, v in ref.items() if k != "boot_files"})
        results.append((score(d), os.path.basename(path)[:-5], d, ref))
    results.sort(key=lambda x: x[0])
    best_score, best_name, diffs, _ref = results[0]

    print(f"probe: {a.iso}")
    print(f"  best match : {best_name}")

    if best_score == 0 and not diffs:
        print("  verdict    : MATCH (byte-identical to the known release)")
        return 0

    real = [(k, e, act) for k, e, act in diffs if not benign_reason(k)]
    benign = [d for d in diffs if benign_reason(d[0])]
    critical = [d for d in real if d[0].startswith(CRITICAL_PREFIXES)]

    explained = []
    for k, e, act in list(real):
        for f, frm, to, recipe in EXPLAINS:
            if k == f and e == frm and act == to:
                explained.append((k, recipe)); real.remove((k, e, act))

    if critical:
        verdict = "DIFFERENT RELEASE"
    elif real:
        verdict = f"MODIFIED ({len(real)} unexplained difference{'s' if len(real) != 1 else ''})"
    elif explained:
        verdict = "MODIFIED (all differences explained by known recipes)"
    else:
        verdict = "MATCH (rebuild of the known release)"
    print(f"  verdict    : {verdict}")

    if explained:
        print("  explained by recipes:")
        for k, recipe in explained:
            print(f"    {k:<44} <- {recipe}")
    if critical:
        print("  CRITICAL -- this is not the release it claims to be:")
        for k, e, act in critical:
            print(f"    {k:<44} expected {e!r}, got {act!r}")
    for k, e, act in real:
        if (k, e, act) in critical:
            continue
        print(f"    {k:<44} expected {e!r}, got {act!r}")
    if a.verbose and benign:
        print("  benign (expected on any rebuild):")
        for k, e, act in benign:
            print(f"    {k:<44} {benign_reason(k)}")
    elif benign:
        print(f"  ({len(benign)} benign rebuild difference(s); -v to list)")

    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
