#!/usr/bin/env python3
"""Validate the YAML fenced in markdown against the schemas recipes are held to.

Issue #3 shipped because nothing ever ran the documented form. `40-schema.sh` validates
recipes/, profiles/, compat/ and schema/; the ```yaml blocks in docs/ were the one place
a wrong example could sit forever. Both docs showed `sign: "your-key-id"` for
iso.checksums while the schema typed `sign` boolean -- a hard validation error nobody
could trip over, because no shipped recipe used the field.

Three things make this possible at all, and each one was measured rather than guessed.

1. ENVELOPE WRAPPING. Most doc blocks are fragments -- a bare list of steps, or a lone
   `vars:` map -- and a fragment is not a document. Each shape is wrapped in the smallest
   legal envelope before validating. The filler step used to satisfy `steps:` must name a
   REAL verb: the first draft invented one and produced twelve false failures.

2. PLACEHOLDER SUBSTITUTION. A few blocks write `sha256: "…"`, which no `^[a-f0-9]{64}$`
   will accept. Dropping the error is wrong -- the failed branch makes
   unevaluatedProperties reject every sibling key, turning 3 complaints into 12. Deleting
   the key is also wrong -- it breaks dependentRequired, inventing "'sha256' is a
   dependency of 'src'". Substituting a value the schema accepts is the only approach that
   reports exactly the real defects, because the block then validates the way a reader
   would expect it to.

3. MIS-TAG DETECTION. A ```yaml block whose top level is a scalar is almost certainly
   shell in the wrong fence.

Prints one `path:line<TAB>message` per failure on stdout; ci/checks/45-doc-yaml.sh turns
each into a fail(). Exit 0 clean, 1 with failures, 2 if this script could not run -- the
wrapper treats 2 as a gate failure, because a checker that dies must not look like a
checker that passed.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

FENCE_OPEN = re.compile(r"^```ya?ml\s*$")

# A scalar that is ENTIRELY one of these means "your value here". A substring ellipsis
# (src: https://…/thing.bin) is deliberately not a placeholder -- narrow on purpose, so
# that `sign: "your-key-id"`, the value that caused #3, is still type-checked and still
# fails.
PLACEHOLDER = re.compile(r"^(?:…|\.\.\.|<[^>]*>)$")

# Tried in order against a failing `pattern`; first match wins. There are eight distinct
# patterns across both schemas and these cover all of them. A future pattern that matches
# none leaves the placeholder in place and the block fails loudly -- the wrong answer here
# is a silent pass, not a false alarm.
CANDIDATES = ["0" * 64, "0644", "https://example.invalid/x", "slax/boot/x", "07-x", "x", "0"]

COERCE = {"boolean": True, "integer": 0, "number": 0, "array": [], "object": {}, "string": "x"}

ENVELOPE = {
    "Recipe": {"apiVersion": "slax-kitchen/v1", "kind": "Recipe",
               "metadata": {"name": "docblock", "summary": "documentation example"}},
    "Profile": {"apiVersion": "slax-kitchen/v1", "kind": "Profile",
                "metadata": {"name": "docblock"},
                "base": {"flavour": "debian", "arch": "64bit", "version": "12.2.0"}},
}
# Any real verb would do; bundle.remove needs only `match`.
FILLER = {"verb": "bundle.remove", "match": "^99-none$"}


def fences(text):
    """Yield (line_number_of_first_body_line, block_text) for each ```yaml block."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if FENCE_OPEN.match(lines[i]):
            start = i + 1
            j = start
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            yield start + 1, "\n".join(lines[start:j])
            i = j
        i += 1


def classify(doc, recipe_top, profile_top):
    """(document to validate, kind) or (None, reason-it-is-not-ours)."""
    if isinstance(doc, dict) and doc.get("kind") in ENVELOPE:
        return doc, doc["kind"]
    if isinstance(doc, list) and doc and isinstance(doc[0], dict) and "verb" in doc[0]:
        return {**ENVELOPE["Recipe"], "steps": doc}, "Recipe"
    if isinstance(doc, dict) and doc:
        meta = doc.get("metadata") or {}
        if set(doc) <= recipe_top and "steps" not in doc:
            env = {**ENVELOPE["Recipe"], "steps": [FILLER], **doc}
            env["metadata"] = {**ENVELOPE["Recipe"]["metadata"], **meta}
            return env, "Recipe"
        if set(doc) <= profile_top:
            env = {**ENVELOPE["Profile"], **doc}
            env["metadata"] = {**ENVELOPE["Profile"]["metadata"], **meta}
            return env, "Profile"
    return None, "not a recipe or profile document"


def _set(doc, path, value):
    cur = doc
    keys = list(path)
    for k in keys[:-1]:
        cur = cur[k]
    cur[keys[-1]] = value


def _get(doc, path):
    cur = doc
    for k in path:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def substitute_placeholders(doc, validator):
    """Replace placeholder scalars with something the schema accepts, in place.

    Driven by the schema's own complaint rather than by a table of field names, so it
    cannot drift when the schema changes. One substitution per pass because each one can
    change which errors the next pass reports.
    """
    for _ in range(12):
        for err in validator.iter_errors(doc):
            if not err.path:
                continue
            value = _get(doc, list(err.path))
            if not (isinstance(value, str) and PLACEHOLDER.match(value)):
                continue
            pick = None
            if err.validator == "pattern":
                pick = next((c for c in CANDIDATES if re.match(err.validator_value, c)), None)
            elif err.validator == "type":
                want = err.validator_value
                pick = COERCE.get(want[0] if isinstance(want, list) else want)
            elif err.validator == "enum" and err.validator_value:
                pick = err.validator_value[0]
            if pick is not None:
                _set(doc, err.path, pick)
                break
        else:
            return doc
    return doc


def main():
    try:
        import jsonschema
        import yaml
    except ImportError as e:
        print(f"doc-yaml: {e}", file=sys.stderr)
        return 2
    try:
        import validate as kitchen_validate
        schema_for_kind = kitchen_validate.SCHEMA_FOR_KIND
    except Exception as e:                                   # noqa: BLE001
        print(f"doc-yaml: cannot read lib/validate.py: {e}", file=sys.stderr)
        return 2

    validators, tops = {}, {}
    for kind in ENVELOPE:
        path = os.path.join(ROOT, "schema", schema_for_kind[kind])
        with open(path) as fh:
            schema = json.load(fh)
        validators[kind] = jsonschema.Draft202012Validator(schema)
        tops[kind] = set(schema["properties"])

    listed = subprocess.run(["git", "-C", ROOT, "ls-files", "*.md"],
                            capture_output=True, text=True)
    if listed.returncode != 0:
        print("doc-yaml: git ls-files failed", file=sys.stderr)
        return 2
    files = [f for f in listed.stdout.split("\n") if f and not f.startswith("vendor/")]

    problems, checked, skipped = [], 0, 0
    for rel in sorted(files):
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for line, body in fences(text):
            where = f"{rel}:{line}"
            try:
                doc = yaml.safe_load(body)
            except yaml.YAMLError as e:
                problems.append((where, f"```yaml block is not valid YAML: "
                                        f"{str(e).splitlines()[0]}"))
                continue
            if doc is None:
                continue
            if not isinstance(doc, (dict, list)):
                problems.append((where, "```yaml block is a bare scalar -- mis-tagged? "
                                        "(shell belongs in a ```sh fence)"))
                continue
            wrapped, kind = classify(doc, tops["Recipe"], tops["Profile"])
            if wrapped is None:
                skipped += 1
                continue
            checked += 1
            v = validators[kind]
            # best_match, not the first error: a failed `then` branch makes
            # unevaluatedProperties complain about every sibling key, so the shallowest
            # error is the cascade and the deepest one is the actual cause. Reporting
            # "'dest', 'from', 'probe' were unexpected" instead of "'/slax/boot/vmlinuz'
            # is not of type 'boolean'" would send a reader to the wrong line.
            errs = list(v.iter_errors(substitute_placeholders(wrapped, v)))
            # Deepest path wins. best_match() does not help here: it prefers the
            # shallow error, which is exactly the cascade.
            err = max(errs, key=lambda e: len(e.absolute_path), default=None)
            if err is not None:
                loc = "/".join(str(x) for x in err.absolute_path)
                problems.append((where, f"{loc or kind}: {err.message}"))

    for where, msg in problems:
        print(f"{where}\t{msg}")
    print(f"doc-yaml: {checked} block(s) checked, {skipped} not recipe/profile documents",
          file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
