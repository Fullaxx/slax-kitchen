#!/usr/bin/env python3
"""Unit tests for the pure half of tests/structure/bundle_assert.py.

The rule under test: a setuid or setgid file that **no package owns** does not belong in a
bundle. Bundles are mounted as root at every boot, and not everything in one comes from a
package -- `bundle.fromTarball` takes an arbitrary archive and refuses setuid members
itself, and this is the backstop for every other route in.

What it must NOT flag is packaged content. Debian's `tor` postinst runs `chmod 02700
/var/lib/tor` on a directory owned by `debian-tor`, and tor refuses to start if that
directory is not owned by the user it drops to. The first version of this rule tested
every entry owned by a non-root uid, directories included, and failed the tor recipe for
doing exactly what its package says to do.
"""
import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

spec = importlib.util.spec_from_file_location(
    "bundle_assert", os.path.join(ROOT, "tests", "structure", "bundle_assert.py"))
ba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba)

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


# (mode, uid, gid, path) exactly as entries() yields them from `unsquashfs -lln`.
# Captured from: unsquashfs 4.6.1
TOR_DIR = ("drwx--S---", 104, 110, "squashfs-root/var/lib/tor")
SU = ("-rwsr-xr-x", 0, 0, "squashfs-root/usr/bin/su")
VENDOR = ("-rwsr-xr-x", 1000, 1000, "squashfs-root/opt/vendor/helper")
ROOT_SETUID = ("-rwsr-xr-x", 0, 0, "squashfs-root/opt/vendor/rooted")
PLAIN = ("-rwxr-xr-x", 0, 0, "squashfs-root/usr/bin/tor")

# As packaged_paths() records them: every path in the /usr form, because Debian 12 is
# usr-merged and dpkg still records /bin/su for a file that lives at /usr/bin/su.
OWNED = {"/var/lib/tor", "/usr/bin/su", "/usr/bin/tor", "/usr/bin/mount"}
SLACK_MOUNT = ("-rwsr-xr-x", 0, 0, "squashfs-root/bin/mount")


def names(rows):
    return [p for _m, _u, _g, p in rows]


def test_packaged_content_is_not_flagged():
    check("tor's data directory", ba.unowned_privileged([TOR_DIR], OWNED), [])
    check("a packaged setuid binary", ba.unowned_privileged([SU], OWNED), [])
    check("an ordinary file", ba.unowned_privileged([PLAIN], OWNED), [])


def test_what_no_package_owns_is_flagged():
    check("a setuid file from nowhere", names(ba.unowned_privileged([VENDOR], OWNED)),
          ["squashfs-root/opt/vendor/helper"])
    # Stricter than the rule it replaces, which only looked at non-root owners: a setuid
    # ROOT binary that no package owns is the more dangerous of the two.
    check("even when it is root's", names(ba.unowned_privileged([ROOT_SETUID], OWNED)),
          ["squashfs-root/opt/vendor/rooted"])
    check("all of them at once", len(ba.unowned_privileged(
        [TOR_DIR, SU, VENDOR, ROOT_SETUID, PLAIN], OWNED)), 2)


def test_a_setgid_directory_is_never_the_question():
    """setgid on a directory means files created inside inherit its group. It hands no
    privilege to anyone, so it is not tested even when no package owns it."""
    scratch = ("drwxrwsr-x", 1000, 1000, "squashfs-root/srv/shared")
    check("unowned setgid directory", ba.unowned_privileged([scratch], OWNED), [])


def test_the_path_mapping_matches_what_the_package_database_records():
    check("prefix stripped", ba.image_path("squashfs-root/usr/bin/su"), "/usr/bin/su")
    check("root itself", ba.image_path("squashfs-root"), "/")
    check("already absolute", ba.image_path("/usr/bin/su"), "/usr/bin/su")

    # THE USR MERGE. Debian 12 records /bin/su in dpkg's list for a file the image has at
    # /usr/bin/su, and Slackware is not merged at all -- it really has /bin/mount. Both
    # sides are compared in the /usr form, so either database answers for either layout.
    check("dpkg's pre-merge path", ba.usr_merged("/bin/su"), "/usr/bin/su")
    check("sbin too", ba.usr_merged("/sbin/mount.ntfs"), "/usr/sbin/mount.ntfs")
    check("lib too", ba.usr_merged("/lib/x86_64-linux-gnu/libc.so.6"),
          "/usr/lib/x86_64-linux-gnu/libc.so.6")
    check("already merged", ba.usr_merged("/usr/bin/su"), "/usr/bin/su")
    check("anything else", ba.usr_merged("/var/lib/tor"), "/var/lib/tor")
    check("a bare directory", ba.usr_merged("/bin"), "/bin")

    # Slackware's binary at /bin/mount matches the record it keeps as bin/mount.
    check("slackware path resolves", ba.unowned_privileged([SLACK_MOUNT], OWNED), [])


def main():
    for fn in [test_packaged_content_is_not_flagged,
               test_what_no_package_owns_is_flagged,
               test_a_setgid_directory_is_never_the_question,
               test_the_path_mapping_matches_what_the_package_database_records]:
        # One test crashing must not stop the rest: the count of failures is only honest
        # if every test ran. The traceback still goes to stderr, because a crash's location
        # is the useful half and a one-line summary loses it.
        try:
            fn()
        except Exception as e:                 # noqa: BLE001
            traceback.print_exc()
            FAILURES.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_bundle_assert.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
