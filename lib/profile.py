#!/usr/bin/env python3
"""Read a build profile and emit shell-assignable settings.

`kitchen build` is orchestration -- it drives unpack, apply, pack and test, which are
already implemented in bash and python respectively. Rather than reimplement a YAML
parser in shell or rewrite the bash halves, this prints `KEY=value` lines for eval.
"""
from __future__ import annotations

import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import target  # noqa: E402
from validate import validate_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_base_iso(base: dict) -> tuple[str, str]:
    """Return (path, how). Explicit `iso:` wins; otherwise sources.yaml states the name.

    LOOKED UP, not rebuilt. The target is `<flavour>-<arch>-<version>` and the ISO is
    `slax-<arch>-<flavour>-<version>.iso` -- the middle two fields are swapped, and that
    swap was written out as a template here while ci/gen-manifests.sh reversed it with a
    sed regex. compat/sources.yaml carries `file:` for every target, so neither spelling
    of the swap has to exist (#36).

    The template survives as the fallback for a `base:` naming no known target, which is
    legal: schema/profile.schema.json constrains flavour and arch to their enums but
    leaves `version` a free string, so a fork pinning an unreleased base still resolves.
    """
    if base.get("iso"):
        p = base["iso"]
        return (p if os.path.isabs(p) else os.path.join(ROOT, p)), "profile"
    try:
        # SystemExit is how target.resolve() refuses; here that is not fatal, it just
        # means this base is not one of the four and the template below is all there is.
        name = target.resolve(
            f"{base['flavour']}-{base['arch']}-{base['version']}")["file"]
    except (SystemExit, RuntimeError):
        name = f"slax-{base['arch']}-{base['flavour']}-{base['version']}.iso"
    return os.path.join(ROOT, "isos", name), "derived"


def base_override(name: str) -> dict:
    """Turn a target name into a base dict, validating it against compat/sources.yaml.

    Checked rather than split-and-hope: `debain-64bit-12.2.0` would otherwise sail
    through, derive a plausible ISO path, and fail much later with "base ISO not found"
    pointing at a filename nobody typed. sources.yaml is the authority on which four
    targets exist, so ask it.

    The asking moved to lib/target.py, which is now the only place that does it -- this
    check was the one of ten target parses that performed it, and the argument above was
    never copied to the other nine (#36).
    """
    spec = target.resolve(name, "--base")
    return {k: spec[k] for k in ("flavour", "arch", "version")}


def main(argv: list[str]) -> int:
    override = None
    argv = list(argv)
    if "--base" in argv:
        i = argv.index("--base")
        if i + 1 >= len(argv):
            print("--base: needs a target, e.g. slackware-64bit-15.0.4", file=sys.stderr)
            return 2
        override = base_override(argv[i + 1])
        del argv[i:i + 2]
    if len(argv) != 2:
        print(f"usage: {argv[0]} <profile.yaml> [--base <target>]", file=sys.stderr)
        return 2
    path = argv[1]
    if not os.path.isfile(path):
        # allow a bare profile name
        cand = os.path.join(ROOT, "profiles", path + ".yaml")
        if os.path.isfile(cand):
            path = cand
        else:
            print(f"profile not found: {argv[1]}", file=sys.stderr)
            return 2

    problems = validate_file(path)
    if problems:
        for p in problems:
            print(f"{path}: {p}", file=sys.stderr)
        return 1

    import yaml
    doc = yaml.safe_load(open(path))
    base = doc["base"]
    if override:
        # Replace outright rather than merge: a profile that pins an explicit `iso:` for
        # one target must not keep pointing at it when asked for a different one.
        base = override
    iso, how = resolve_base_iso(base)
    out = doc.get("output", {}) or {}
    name = out.get("name") or f"slax-{doc['metadata']['name']}-{base['version']}.iso"
    for k, v in (("{{version}}", base["version"]), ("{{flavour}}", base["flavour"]),
                 ("{{arch}}", base["arch"]), ("{{name}}", doc["metadata"]["name"])):
        name = name.replace(k, v)

    emit = {
        "PROFILE_PATH": path,
        "PROFILE_NAME": doc["metadata"]["name"],
        # Assembled ONCE, here. lib/build.sh built the work-tree path out of the three
        # fields and ci/tier-c.sh reassembled the same string from the profile with no
        # check; both read this now (#36).
        "BASE_TARGET": f"{base['flavour']}-{base['arch']}-{base['version']}",
        "BASE_FLAVOUR": base["flavour"],
        "BASE_ARCH": base["arch"],
        "BASE_VERSION": base["version"],
        "BASE_ISO": iso,
        "BASE_ISO_SOURCE": how,
        # NAMES ONLY. build.sh uses this to decide test expectations and to print what
        # will run; the authoritative list -- including any per-recipe vars -- goes to
        # apply.py via --profile, because a space-joined shell string cannot carry them.
        "RECIPES": " ".join(r if isinstance(r, str) else r["name"]
                            for r in doc["recipes"]),
        "OUTPUT_NAME": name,
        "OUTPUT_HYBRID": "1" if out.get("hybrid") else "",
        "OUTPUT_BACKEND": out.get("backend", ""),
        "TESTS": " ".join(doc.get("test", []) or []),
    }
    for k, v in emit.items():
        print(f"{k}={shlex.quote(str(v))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
