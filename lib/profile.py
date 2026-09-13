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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <profile.yaml>", file=sys.stderr)
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
        "RECIPES": " ".join(doc["recipes"]),
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
