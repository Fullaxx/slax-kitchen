#!/usr/bin/env python3
"""tools/build-busybox.sh, driven with a stub container -- no docker, no network.

WHY THIS EXISTS. The script has no `set -e` (the container's exit code is read by hand),
so every step the final "here is your binary" line depends on is guarded by hand too. One
of those guards was written inverted:

    if ! python3 - … <<'PY' … PY
    then :; else <error>; exit 1; fi

`! cmd` is true when cmd FAILS, so the error path ran on every SUCCESSFUL build -- deleting
the build claim it had just written correctly -- and a python3 that really failed fell into
`:` and was ignored, which is the exact failure the guard was added for. Nothing caught it
because building busybox needs docker, so nothing ran the script.

A stub `docker` on PATH is enough to reach the claim step: the real one is invoked once, as
`docker run … sh -s <version> <sha>` with the container script on stdin and a tar on stdout.
The stub answers with that tar. The binary in it is not a real i386 busybox, so the script
still exits 1 at its `file -b` assertion further down -- after the claim is written, which
is the part under test here.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
SCRIPT = os.path.join(ROOT, "tools", "build-busybox.sh")

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def stub_dir(tmp, python3_fails=False):
    """A PATH directory holding the fakes this test needs."""
    binp = os.path.join(tmp, "bin")
    os.makedirs(binp, exist_ok=True)

    payload = os.path.join(tmp, "payload.tar")
    with tarfile.open(payload, "w") as t:
        for name, data in (("busybox", b"not really a binary\n"),
                           (".config", b"CONFIG_STATIC=y\n"),
                           ("alpine-release", b"3.19.9\n"),
                           ("apk-versions", b"musl-dev-1.2.4_git20230717-r6\nmake-4.4.1-r2\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, __import__("io").BytesIO(data))

    write(os.path.join(binp, "docker"), f'''#!/bin/sh
# Swallow the container script on stdin and answer with what the real build would hand back.
cat > /dev/null
exec cat {payload!r}
''')
    if python3_fails:
        write(os.path.join(binp, "python3"), '''#!/bin/sh
cat > /dev/null
echo "stub python3: refusing to write anything" >&2
exit 3
''')
    return binp


def write(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run(tmp, binp):
    out = os.path.join(tmp, "busybox-test")
    env = dict(os.environ, PATH=binp + os.pathsep + os.environ["PATH"])
    r = subprocess.run(["sh", SCRIPT, "-o", out], capture_output=True, text=True, env=env)
    return r, out


def test_a_successful_build_keeps_the_claim_it_wrote():
    tmp = tempfile.mkdtemp()
    try:
        r, out = run(tmp, stub_dir(tmp))
        said = r.stdout + r.stderr
        check("no false alarm", "the build claim could not be written" in said, False)
        check("the claim is still there", os.path.isfile(out + ".provenance.json"), True)
        if os.path.isfile(out + ".provenance.json"):
            claim = json.load(open(out + ".provenance.json"))
            check("and describes this binary", claim["artifact"]["sha256"], sha256_of(out))
            check("with the container it used",
                  claim["container"]["alpine_release"], "3.19.9")
        # The stub's "binary" is not an i386 ELF, so the script correctly refuses it --
        # AFTER the claim step, which is what this test is about.
        check("the type assertion still fires", "wrong binary type" in said, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_claim_that_cannot_be_written_fails_the_build():
    tmp = tempfile.mkdtemp()
    try:
        r, out = run(tmp, stub_dir(tmp, python3_fails=True))
        said = r.stdout + r.stderr
        check("it says so", "the build claim could not be written" in said, True)
        check("with python3's own status", "exited 3" in said, True)
        check("and stops", r.returncode, 1)
        check("leaving no claim to believe", os.path.exists(out + ".provenance.json"), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if not shutil.which("tar"):
        # 77, NOT 0, AND THAT IS THE WHOLE POINT OF SAYING SO. A skip is not a pass, and
        # returning 0 made the two below indistinguishable from tests that ran and agreed
        # -- to the gate, which only reads the status, and to anyone reading it here.
        # ci/unit-run.py reads this status: it reports the skip, and waives its "every
        # test defined here ran" measurement for this run, which would otherwise report
        # both of them as never having run. Which is true, and not the answer wanted.
        print("tests/unit/test_build_busybox.py: no tar; skipped")
        return 77
    for fn in [test_a_successful_build_keeps_the_claim_it_wrote,
               test_a_claim_that_cannot_be_written_fails_the_build]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_build_busybox.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
