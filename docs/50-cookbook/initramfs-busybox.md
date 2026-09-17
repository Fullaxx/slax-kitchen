# `initramfs-busybox` — replace the 2017 busybox

```sh
tools/build-busybox.sh --check-parity     # ~30 s, needs docker
kitchen apply initramfs-busybox
```

**Status: boot-verified** — all four [Tier C](../60-testing/tier-c.md) paths, including persistence
across two boots, with all three livekit markers each time. Early boot's `find_data`,
`mount_bundles`, `init_union`, `union_append_bundles`, `persistent_changes` and `change_root` all
ran on the new binary. Gates 1-3 and 5 run in CI on every push.

CI builds and structurally asserts it on all four targets every run, but **does not boot it** — the
boot job builds only the `example` and `boot-matrix` profiles. If you change the busybox version or
the config, redo the boot test.

## Gate 4 — the boot half

```sh
kitchen build boot-matrix --keep --force
kitchen apply recipes/available/initramfs-busybox.yaml -w work/boot-matrix
kitchen pack -s work/boot-matrix/iso -o out/bb.iso --hybrid --uefi --force
ci/tier-c.sh --iso out/bb.iso --profile boot-matrix-busybox --target debian-64bit-12.2.0
```

That is the whole of gate 4: all four [Tier C](../60-testing/tier-c.md) paths with the
candidate in the initramfs. The persistence pair is the point of it — two boots on one
disk is the hardest exercise of `losetup`, `df` and `date` in the system, and those are
exactly the applets gate 2 diffs statically.

**Run on 2026-09-16 at `a61fcde`, `debian-64bit-12.2.0`, under KVM: 5 boots, 37 s (before the UEFI lead widened), all
green.** And the result worth reporting is a diff. Against the same image built with the
stock 2017 binary, the assembled filesystem differs by **one line**:

```diff
- busybox: BusyBox v1.26.2
+ busybox: BusyBox v1.37.0
```

Union type, bundle list, kernel, package count and every reported file are identical.
Replacing the initramfs busybox changes the binary and nothing else about what the
machine assembles — which is the claim this recipe needs and could not previously make.

Getting there found three unrelated bugs, all in code paths nothing had run: relative
`-w` broke every initramfs verb, `kitchen build --force` could not overwrite an ISO, and
a passing test erased a failing one in the same build.

## Gate 5, and the bug it found

The five-gate harness this recipe is gated on had only three gates written. Gate 4 is a
real boot and lives in [Tier C](../60-testing/tier-c.md). **Gate 5 — the rollback proof —
was never a host task at all**: it was filed in the deferred queue as part of "busybox
gates 4-5 + a real-hardware bench", while its actual definition is *re-apply with the
stock blob and confirm the initramfs comes back, seconds*. No KVM, no hardware. It runs
in CI now, per target:

```sh
sudo tests/busybox/gates.sh build/busybox-1.37.0-i386-static build/irfs build/initrfs.img
```

It hands `lib/apply.py` the **stock** binary extracted from the image it is rolling back
to, and compares the repacked tree against pristine — every path, mode, symlink target
and file hash. Not the cpio container: archive order and timestamps are not reproducible
and are not what reversibility means.

**It failed on its first run, by one entry.** The stock initramfs ships
`bin/init -> ../init`, and applying this recipe deleted it. Upstream's `initramfs_create`
does two things and the verb had copied only the first:

```sh
:68   rm -f $INITRAMFS/{s,}bin/init      # drop the symlink busybox --install would make
:155  ln -s ../init $INITRAMFS/bin/init  # then point one at the REAL /init script
```

`init` is a busybox applet, so a generated `bin/init` would shadow the real `/init`
script — hence line 68. Line 155 puts a different link back. The verb now does both, and
gate 5 reports 248 entries restored exactly.

The failure worth worrying about was the other one: if upstream shipped a **curated**
symlink set, the verb would generate one link per `busybox --list` applet and leave
extras behind. Measured on `debian-64bit`: 248 applets, 245 symlinks, and the difference
is exactly the three applets shadowed by real files (`busybox`, `blkid`, `eject`). Not
curated, so a rollback is clean.

## Why

`BusyBox v1.26.2 (2017-12-14)`, byte-identical on all four ISOs. Among what that means in practice:

| | |
|---|---|
| CVE-2017-16544 | terminal-escape RCE via `ash` tab completion — and `/init` calls `debug_shell` **six times** |
| CVE-2022-48174 | `ash` stack overflow |
| pre-`CONFIG_TIME64` | `date -r` on a post-2038 mtime already misbehaves today |

## One build serves all four targets

`bin/busybox` is byte-identical across the four images, and so is everything else in the initramfs
**except `lib/modules/<ver>/`**. It is **i386 even on the 64-bit images** — which is where the
`CONFIG_IA32_EMULATION` requirement comes from that any replacement kernel inherits.

So there is one binary to build, not four.

## The binary is not in this repository

`ci/checks/00-no-binaries.sh` rejects it, and that gate is right: a 1.2 MB blob that runs as root at
boot is exactly what should be built from pinned source rather than committed and forgotten.

`tools/build-busybox.sh` builds it in about 30 seconds inside `i386/alpine`, verifying upstream's
published sha256 **before compiling a line**. The container is needed because this host has neither
a 32-bit libc nor musl; nothing is bind-mounted, because the docker daemon may not share the
filesystem — the script goes in on stdin and the results come out on stdout, as a tar.

It writes three files, not one:

| file | what |
|---|---|
| `build/busybox-1.37.0-i386-static` | the binary |
| `….config` | the resulting `.config`, after `defconfig` and the deltas below |
| `….provenance.json` | the **build claim**: the binary's sha256, the busybox tarball's URL and sha256, the image **by digest**, `/etc/alpine-release`, and the version of every apk package the build installed |

The image is pinned by digest; the apk packages are not — Alpine's stable branch takes security
updates — so their versions are recorded rather than promised. When the recipe installs the binary,
`initramfs.busybox` copies the claim into the image's provenance and marks it **verified** only if the
claim's sha256 is the sha256 of the binary it actually used.

CI builds it too, cached on the script's own hash.

### Not a checked-in `.config`

A 1000-line `.config` pins every symbol, says nothing about intent, and has to be regenerated
wholesale on a version bump. The script starts from the tarball's own `defconfig` and flips a named,
commented list:

| symbol | why |
|---|---|
| `CONFIG_STATIC=y` | the initramfs has no dynamic loader |
| `CONFIG_LONG_OPTS=y` | `livekitlib` calls `date --date "$1" '+%s'` |
| `CONFIG_MODPROBE_SMALL=n` | the small implementation handles aliases and blacklists differently, and `modprobe_everything()` fires it hundreds of times per boot. Alpine ships full modutils for the same reason |
| `CONFIG_AR`, `UNLZOP`, `LZOPCAT` | `default n` in 1.37.0; enabled purely for applet parity |

**Builds are not byte-reproducible.** busybox bakes the build time into its banner and does not
honour `KBUILD_BUILD_TIMESTAMP` — verified by building twice and comparing. What is pinned is the
*source*: the tarball sha256 and the config deltas.

## Applet parity, measured

| | |
|---|---|
| 1.26.2 | 248 applets |
| 1.37.0 | **406** |
| lost | **`catv`** — and nothing else |

`catv` was removed upstream and is unused by Slax. All **52** distinct commands that `/init`,
`livekitlib` and `/shutdown` actually invoke are present.

## Three things the symlink regeneration must get right

Swapping the binary alone leaves 245 symlinks describing an applet set that no longer matches it.
That is why this is a verb and not an `initramfs.files` entry.

1. **`blkid` and `eject` are real files that shadow busybox applets**, and `livekitlib` parses
   `blkid -o full` — an option busybox's applet does not have. A symlink must never overwrite an
   existing file. The verb refuses to finish if either has stopped being a real binary.
2. **`bin/init` must not exist**, or busybox's `init` applet shadows the `/init` script and the
   machine boots the wrong thing. (1.38.0 adds `nuke` and `linuxrc`, which deserve the same
   treatment.)
3. **Stale symlinks must go.** The shipped tree has a `catv` symlink; 1.37.0 has no such applet.
   Leaving it gives a `catv` in `PATH` that fails at runtime rather than being absent. Upstream
   never had to think about this because it builds the tree from empty — this edits one in place.

```
busybox 739,784 -> 1,213,688 bytes, 248 -> 406 applets
applets removed upstream: catv
symlinks: +159 new, -1 stale, 2 real files left alone
  blkid: still a real binary, as it must be
```

Result: **403 applet symlinks + 8 real binaries**, 0 dangling, 0 orphaned, and the 7 device nodes
intact.

## It fixes upstream's symlink generation too

`initramfs_create` derives the applet list by scraping busybox's **human-readable usage text**:

```sh
$INITRAMFS/bin/busybox | grep , | grep -v Copyright | tr "," " " | ...
```

That is not a stable interface — see [issue 10](../30-inventory/known-upstream-bugs.md). This verb
uses `busybox --list`, which is documented and which even the shipped 1.26.2 answers correctly.

Reading the list requires **executing an i386 binary on the build host**. If yours cannot, the verb
says so plainly — and that is the same limitation a kernel without `CONFIG_IA32_EMULATION` has.

## Cost

The initramfs grows 8,872,472 → 9,152,792 bytes (**+280 KB**); the ISO stays 415 MiB.
