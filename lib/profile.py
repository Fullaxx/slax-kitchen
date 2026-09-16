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
from validate import validate_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_base_iso(base: dict) -> tuple[str, str]:
    """Return (path, how). Explicit `iso:` wins; otherwise use the standard filename."""
    if base.get("iso"):
        p = base["iso"]
        return (p if os.path.isabs(p) else os.path.join(ROOT, p)), "profile"
    name = f"slax-{base['arch']}-{base['flavour']}-{base['version']}.iso"
    return os.path.join(ROOT, "isos", name), "derived"


def base_override(target: str) -> dict:
    """Turn a target name into a base dict, validating it against compat/sources.yaml.

    Checked rather than split-and-hope: `debain-64bit-12.2.0` would otherwise sail
    through, derive a plausible ISO path, and fail much later with "base ISO not found"
    pointing at a filename nobody typed. sources.yaml is the authority on which four
    targets exist, so ask it.
    """
    import yaml
    src = os.path.join(ROOT, "compat", "sources.yaml")
    known = list((yaml.safe_load(open(src)) or {}).get("targets", {}))
    if target not in known:
        raise SystemExit(f"--base: unknown target {target!r}\n"
                         f"  known: {', '.join(sorted(known))}")
    flavour, arch, version = target.split("-", 2)
    return {"flavour": flavour, "arch": arch, "version": version}


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
