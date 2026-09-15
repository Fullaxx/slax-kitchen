# Editing bundles

The core technique: **bootstrap the build chroot from the ISO's own bundles.** `01-core.sb` *is* a
complete Debian 12 / Slackware 15 root filesystem with `apt` / `pkgtools` already inside it, so
there is no need for `debootstrap` or an external base image — and the result is guaranteed to match
the shipped kernel's ABI.

```
1. unsquashfs the bundle              (in place, straight out of the ISO)
2. add the runtime dirs livekit makes at boot
3. chroot, install packages
4. diff the tree                      (added AND modified)
5. mksquashfs the delta -> 07-<name>.sb
```

## unsquashfs reads the bundle in place

`unsquashfs -o <offset>` takes a byte offset, so a bundle can be read **directly out of the ISO**
without extracting a 122 MB `.sb` first. `lib/isoparse.py` supplies the offset:

```sh
unsquashfs -o 137216 -d work/core isos/slax-64bit-debian-12.2.0.iso
```
01-core extracts to ~609 MB / 18,671 files in about two seconds.

## A bundle is not a complete root filesystem

This bites immediately. `01-core.sb` ships **no** `/tmp`, `/run`, `/proc`, `/sys`, `/dev`, `/media`,
`/mnt` or `/boot` — livekit creates them at boot, in `change_root()`:

```sh
mkdir -p boot dev proc sys tmp media mnt run
chmod 1777 tmp
if [ ! -e dev/console ]; then mknod dev/console c 5 1; fi
```

Without `/tmp`, `apt-get update` fails with `Unable to mkstemp /tmp/clearsigned.message...`. So the
build has to recreate them, and then **exclude them again** from the output bundle — exactly the
paths upstream's own `savechanges` excludes.

## Unprivileged chroot: the experiment, and the verdict

The dev container has no `CAP_SYS_ADMIN`, so the original plan was to reach for `proot`. Both
unprivileged options were tested and **both failed**; real `chroot` turned out to be unnecessary to
avoid.

**`proot` 5.1.0 — rejected, and dangerous.** It does not translate the `statx()` syscall, so any
`stat` of a path escapes the fake root and hits the **host** filesystem. Demonstrated directly:

```
$ proot -0 -r <bundle> sh -c 'stat /etc/lsb-release'
    reports it EXISTS   <- the host's file; the bundle has no such path
```

`cat` works while `stat` fails, which makes the failure silent and selective: paths that happen to
exist on the host appear to work, and only paths unique to the bundle fail. The GPG errors seen
during `apt-get update` under proot (`keyring ... not readable by user '_apt'`) were a symptom of
exactly this. A build tool that silently reads the wrong filesystem can emit a corrupt bundle, so
this is not a workaround-able quirk.

**`fakechroot` — rejected.** It works by `LD_PRELOAD`ing **host** binaries against the **target's**
libraries, so it needs compatible glibc on both sides — and on *every* target, which no single host
gives you here. Measured on the default `ubuntu:24.04` container, glibc 2.39 against a Debian 12
bundle's 2.36 fails outright:

```
/usr/sbin/chroot: .../libc.so.6: version `GLIBC_2.38' not found
```

**Real `chroot` — works, and is what we use.** `CAP_SYS_CHROOT` is present even without
`CAP_SYS_ADMIN`, and `CAP_MKNOD` lets us create the device nodes. Notably **`/proc` does not need to
be mounted**: `apt-get update` and a full `apt-get install` with maintainer scripts and triggers
(`man-db`, `libc-bin`) both complete with exit 0 without it.

Two pieces of ordinary chroot hygiene are still required:

```sh
printf '#!/bin/sh\nexit 101\n' > usr/sbin/policy-rc.d   # do not start services
cp /etc/resolv.conf etc/resolv.conf                      # the bundle's points at 8.8.8.8
```

## The delta must catch modified files, not just new ones

The obvious diff — compare two lists of filenames — is **wrong**, and wrong in a way that looks
fine. Installing `tmux` and `ncdu` into 01-core produced:

| Method | Delta found |
|---|---|
| filenames only | 57 |
| size + mtime + mode manifest | **85 added + 31 modified** |

The 31 modified files include `var/lib/dpkg/status`. Detecting modification matters in general —
miss it and a bundle ships files whose *changed* form never makes it out. Upstream's `savechanges`
never hits this, because it reads the aufs *changes* layer where a modified file is physically
present as a copy-up; an offline diff has to look for it.

**But `dpkg/status` itself must not ship**, and that is the exception worth knowing. It is a single
file holding whole-system state, and a union composes trees rather than files, so the highest copy
wins outright — a bundle built from a short stack would replace the real 600-package database with
its own 299. `kitchen` excludes it and ships a fragment in
`var/lib/slax-kitchen/dpkg-status.d/<bundle>` instead, which `pack` merges. See
[composing bundles](composing-bundles.md).

Verified in the built bundle:

```
squashfs-root/usr/bin/tmux
squashfs-root/usr/bin/ncdu
squashfs-root/var/lib/dpkg/info/tmux.list           <- a DIRECTORY, so it unions correctly
squashfs-root/var/lib/slax-kitchen/dpkg-status.d/07-extras   <- the 3 packages this bundle added
```

## Build the bundle with upstream's exact parameters

```sh
mksquashfs delta/ 07-tools.sb -comp xz -b 1024K -Xbcj x86 -always-use-fragments -noappend
```

This is what `create_bundle()` in `livekitlib`, `dir2sb` and `savechanges` all use. Verify the
result matches the stock bundles:

```
v4.0 xz block=1048576 flags=0x04e0 dict=1048576 bcj=x86
flag names: ALWAYS_FRAGMENTS DUPLICATES EXPORTABLE COMPRESSOR_OPTIONS
```

## Numbering, and why 07

Load order is the numeric prefix, and **higher wins** — `union_append_bundles` inserts each branch at
aufs index 1, so each insertion outranks the previous. Stock bundles run `01`–`06`, and `savechanges`
writes `99-changes-N.sb`. A new bundle at `07` therefore overrides everything stock while still
losing to a user's saved session, which is almost always what you want.

## Slackware is a different animal

The Debian path is straightforward. Slackware needed four separate fixes, all of them cases where
something reported success while doing nothing:

**1. `-batch=on` does not silence every prompt.** The stock mirror is `slackware64-current` while the
base reports `15.0+`, so slackpkg asks *"Is this really what you want?"*. With no stdin it takes the
default (No), installs nothing, and **exits 0**. This produced a cheerful `built 07-extras.sb
(4 KiB)` containing no packages. Feed `y` on stdin.

**2. `slackonly.com` is NXDOMAIN.** Slax 15.0.4 ships `REPOPLUS=( slackpkgplus slackonly )`, and
that host no longer resolves. `slackpkg update` fails on an unreachable repo, so a stock Slackware
Slax can install nothing at all today. `bundle.packages` resolves every `MIRRORPLUS` host and prunes
dead repos from `REPOPLUS` first. Expect more of this: the release has been dormant since 2023.

**3. slackpkg's exit code is unreliable in *both* directions.** It returns 0 after declining its own
prompt and doing nothing, and returns **1 after a completely successful update** — it prints a
"you are running 15.0+, be sure to run these steps" advisory and exits non-zero. So `update` is
judged by whether `var/lib/slackpkg/PACKAGES.TXT` actually landed, not by `$?`.

**4. Verify against the package database, always.** After any install, `bundle.packages` checks
`dpkg-query` (Debian) or `/var/lib/pkgtools/packages/` (Slackware) for every requested package and
fails loudly if one is missing. Parsing the Slackware side needs care: entries are
`<name>-<version>-<arch>-<build>`, and the name may itself contain hyphens, so strip exactly the last
three fields — a prefix match would let `gcc` match `gcc-g++-13.2.0-x86_64-1`.

Two more differences worth knowing:

- **Slackware does no dependency resolution.** Neither `installpkg` nor `slackpkg` pulls deps in —
  that is Slackware's design, not a limitation here. Name every dependency explicitly.
- **Package names differ.** `ncdu` is in Debian proper but only in third-party Slackware repos, so
  the `add-packages` template uses `when: flavour==debian` / `when: flavour==slackware` to carry a
  different list per flavour.

## Mirror pinning

Debian 12 is oldstable, and Slackware `-current` has moved a long way past the 2023 snapshot Slax was
built from. Unpinned installs will drift from the base. Pin to `snapshot.debian.org` or a dated
Slackware mirror when reproducibility matters.
