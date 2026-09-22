#!/usr/bin/env python3
"""The one authority on what a target name is, and on compat/sources.yaml's `targets:`.

A target is `<flavour>-<arch>-<version>`, e.g. debian-64bit-12.2.0. sources.yaml says
which four exist and what each one's ISO is called; everything else about a target is
derived from that file and nowhere else.

WHY THIS MODULE EXISTS. Ten sites parsed or assembled the triple and exactly one asked
sources.yaml whether the name was real -- lib/profile.py's base_override(), which argues
the case in its own docstring and was never copied. Two of the nine that did not were
gates, and both failed open:

  - ci/recipe-matrix.sh split with ${TARGET%%-*} and a `case` whose catch-all made every
    unrecognised target 64-bit. `debain-64bit-12.2.0` declared all 35 recipes
    incompatible with a flavour that does not exist, skipped every one, and exited 0 in
    two seconds -- the gate that exists to catch a recipe which only works on one target,
    reporting green having run nothing.
  - ci/tier-c.sh accepted any --target, and merged the ledger on `!=`, so a misspelt one
    appended rather than replaced. 97-tier-c-ledger.sh has closed sets for path, result
    and accel but typed `target` as str, so it passed -- and announced "5 target(s)".
    ci/release-notes.sh derives the published Tier C claim from that file, so a release
    would have named a target that does not exist across 21 boots that were 20.

Issue #36. The shape is the one #35 settled on for facts: one function answers, every
caller reads it, and two probes that agree today are still two probes.

THE FILENAME IS LOOKED UP, NEVER REBUILT. The target is flavour-arch-version and the ISO
is `slax-<arch>-<flavour>-<version>.iso` -- the two middle fields are swapped. That swap
was spelled out in a template in one place and reversed with a sed regex in another;
sources.yaml states `file:` outright, so neither is needed.

`import yaml` is inside the functions, as everywhere else in lib/: tests/unit run on a
stock python3 with no PyYAML (see tests/unit/test_release.py's sources_targets), so a
module-level import would break them on import alone.
"""
from __future__ import annotations

import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "compat", "sources.yaml")

# The fields sources.yaml carries for every target, checked on the way out so a
# half-written entry is an error here rather than a KeyError in a caller.
TARGET_FIELDS = ("file", "tree", "size", "sha256")


def load() -> dict:
    """compat/sources.yaml, or a RuntimeError naming it."""
    import yaml
    if not os.path.isfile(SOURCES):
        raise RuntimeError(f"missing {SOURCES}")
    return yaml.safe_load(open(SOURCES)) or {}


def names() -> list[str]:
    """The target names that exist, sorted. The closed set every gate should hold to."""
    return sorted((load().get("targets") or {}))


def resolve(name: str, where: str = "") -> dict:
    """One target's fields, or SystemExit naming the ones that exist.

    `where` is the flag or argument the name arrived on, so the refusal says which input
    to fix. Without it "unknown target 'debain-64bit-12.2.0'" leaves the reader to work
    out whether that came from --base, --target or argv[1].

    REFUSES rather than splits. `debain-64bit-12.2.0` has the right shape and splits
    cleanly into three plausible fields -- which is exactly how it reached a recipe
    matrix that skipped all 35 recipes and passed (#36). Shape is not existence.

    `target` is carried in the returned dict so a caller that has been handed one of
    these never has to reassemble the string it came from.
    """
    targets = load().get("targets") or {}
    spec = targets.get(name)
    if spec is None:
        raise SystemExit(f"{where + ': ' if where else ''}unknown target {name!r}\n"
                         f"  known: {', '.join(sorted(targets))}")
    missing = [f for f in TARGET_FIELDS if f not in spec]
    if missing:
        raise SystemExit(f"target {name!r} in compat/sources.yaml is missing: "
                         f"{', '.join(missing)}")
    flavour, arch, version = name.split("-", 2)
    return {"target": name, "flavour": flavour, "arch": arch, "version": version,
            **{f: spec[f] for f in TARGET_FIELDS}}


def from_iso_name(path: str) -> dict:
    """The target whose `file:` is this ISO's basename, or SystemExit.

    A LOOKUP, not a parse. ci/gen-manifests.sh recovered the target from the filename
    with `sed 's/^slax-\\([0-9]*bit\\)-\\([a-z]*\\)-\\(.*\\)$/\\2-\\1-\\3/p'`, which
    reverses the arch/flavour swap by hand and accepts anything shaped like it:
    slax-64bit-debian-999.9.9.iso became debian-64bit-999.9.9 and four manifests were
    written under that name. sources.yaml already states every real filename.
    """
    base = os.path.basename(path)
    for name, spec in (load().get("targets") or {}).items():
        if spec.get("file") == base:
            return resolve(name)
    raise SystemExit(
        f"no target in compat/sources.yaml is built from {base!r}\n"
        f"  known: {', '.join(sorted(names()))}\n"
        f"  A NEW RELEASE IS REGISTERED IN sources.yaml FIRST -- file, tree, size and\n"
        f"  sha256 -- and everything else follows from that entry: `kitchen fetch`\n"
        f"  downloads it, `kitchen probe` compares against it, and the manifests and\n"
        f"  the Tier C ledger are named by it.")


def emit(spec: dict) -> None:
    """Print shell-assignable settings, the way lib/profile.py does, for `eval`."""
    out = {"BASE_TARGET": spec["target"], "BASE_FLAVOUR": spec["flavour"],
           "BASE_ARCH": spec["arch"], "BASE_VERSION": spec["version"],
           "BASE_ISO_NAME": spec["file"], "BASE_ISO_SHA256": spec["sha256"],
           "BASE_ISO_SIZE": str(spec["size"]), "BASE_TREE": spec["tree"]}
    for k, v in out.items():
        print(f"{k}={shlex.quote(str(v))}")


def main(argv: list[str]) -> int:
    """A missing or unreadable sources.yaml is a refusal, not a traceback.

    load() raises RuntimeError, and an uncaught one printed twenty lines of stack above
    the gate's own FAIL line -- which is the message somebody actually has to read.
    """
    try:
        return _main(argv)
    except RuntimeError as e:
        print(f"{os.path.basename(argv[0])}: {e}", file=sys.stderr)
        return 2


def _main(argv: list[str]) -> int:
    args = argv[1:]
    if args[:1] == ["--list"]:
        for n in names():
            print(n)
        return 0
    if args[:1] == ["--from-iso"] and len(args) == 2:
        emit(from_iso_name(args[1]))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        emit(resolve(args[0]))
        return 0
    print(f"usage: {os.path.basename(argv[0])} <target> | --from-iso <iso> | --list",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
