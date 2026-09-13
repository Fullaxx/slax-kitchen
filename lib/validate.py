#!/usr/bin/env python3
"""Validate recipes, profiles and fingerprints against their JSON Schemas.

Called by the 40-schema commit gate and by `kitchen apply` before a recipe runs --
a malformed recipe should fail at the gate, not halfway through mutating a work tree.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_FOR_KIND = {
    "Recipe": "recipe.schema.json",
    "Profile": "profile.schema.json",
}


def validate_file(path: str) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    import yaml
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema not installed (pip install jsonschema)"]

    try:
        doc = yaml.safe_load(open(path))
    except yaml.YAMLError as e:
        return [f"not valid YAML: {e}"]
    if not isinstance(doc, dict):
        return ["top level must be a mapping"]

    kind = doc.get("kind")
    if kind == "Fingerprint":
        return []                       # generated; schema is advisory only
    if kind not in SCHEMA_FOR_KIND:
        return [f"unknown kind {kind!r} (want one of {sorted(SCHEMA_FOR_KIND)})"]

    schema_path = os.path.join(ROOT, "schema", SCHEMA_FOR_KIND[kind])
    if not os.path.isfile(schema_path):
        return [f"schema missing: {schema_path}"]
    import json
    schema = json.load(open(schema_path))

    problems = []
    v = jsonschema.Draft202012Validator(schema)
    for err in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        problems.append(f"{where}: {err.message}")

    # Cross-checks the schema cannot express.
    if kind == "Recipe":
        stem = os.path.basename(path).rsplit(".", 1)[0]
        name = doc.get("metadata", {}).get("name")
        if name and name != stem:
            problems.append(
                f"metadata.name {name!r} does not match filename stem {stem!r} "
                "(recipes are referenced by name, so these must agree)")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {argv[0]} <file.yaml>...", file=sys.stderr)
        return 2
    rc = 0
    for path in argv[1:]:
        problems = validate_file(path)
        if problems:
            rc = 1
            for p in problems:
                print(f"{path}: {p}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
