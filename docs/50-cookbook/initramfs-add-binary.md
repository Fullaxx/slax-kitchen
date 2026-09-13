# `initramfs-add-binary` — put a tool in early boot

**Status: verified** — applied and booted on 64-bit Debian; the addition survives into the rebuilt
ISO and the image still reaches `slax login:`.

```sh
kitchen apply initramfs-add-binary
```

## When you need this rather than a bundle

The initramfs runs **before any bundle is mounted**. Anything you want during `find_data`,
`persistent_changes`, or a `debug` shell has to be in the initramfs — a bundle is far too late.

| you want it during | put it in |
|---|---|
| `find_data`, persistence setup, a `debug` shell | **the initramfs** |
| the running system | a bundle, or [`rootcopy`](rootcopy-overlay.md) |

## Two hard constraints

Both are checked and reported, because both fail in ways that are hard to diagnose.

**Static only.** There is no dynamic loader in the initramfs. A dynamically linked binary fails with
`not found` — naming the *interpreter*, not the binary, which sends people looking in the wrong
place. The verb tells you up front:

```
initramfs: + /bin/dyn-test  [x86-64, DYNAMIC -- the initramfs has no loader; this will not run]
```

**i386 is the portable choice.** Every one of the eight shipped binaries is i386 *even on the 64-bit
images* — which is why the kernel is built with `CONFIG_IA32_EMULATION`. An x86-64 static binary
works, but only on the two 64-bit targets:

```
initramfs: + /bin/mytool  [x86-64 static -- 64-bit targets only; i386 works on all four]
initramfs: + /bin/mytool  [i386 static]
```

Neither is refused — a data file is a legitimate thing to add, and so is a deliberately
64-bit-only tool. They are reported so the choice is deliberate.

## What it refuses

```
initramfs.files: bin/blkid shadows a standalone binary livekitlib depends on.
Pass force: true if you really mean to replace it.
```

`blkid` and `eject` are real binaries occupying names busybox also provides as applets, and
`livekitlib` parses `blkid -o full` — an option busybox's applet does not have. Overwriting either
breaks device detection at boot with **no diagnostic at all**. See
[initramfs-userland](../30-inventory/initramfs-userland.md).

The image is left untouched when a step fails, so a refused apply costs you nothing.

## Writing your own

```yaml
- verb: initramfs.files
  files:
    - dest: /bin/mytool
      src: ./mytool            # relative to the recipe file
      mode: "0755"
    - dest: /etc/mytool.conf
      content: |
        key = value
```

`src` copies a file; `content` writes one inline. `mode` is octal as a string.

## How it is repacked

Upstream's exact pipeline, plus one addition:

```sh
find . -print | LC_ALL=C sort | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
```

**`--check=crc32` is not optional** — the kernel's xz decoder cannot do CRC64, which is xz's
default, and getting it wrong produces a reboot loop with no message. `kitchen` verifies the check
type after packing and refuses to ship an image that fails it.

The `LC_ALL=C sort` is ours. Upstream has no sort, so archive order follows readdir order and is not
stable — on overlayfs, five runs over an unchanged tree gave five different orders. Sorting changes
nothing at boot and makes a repack diffable.
→ [reproducibility](../40-workflow/reproducibility.md)

## Verifying

```sh
kitchen pack && kitchen test out/*.iso --kernel --seconds 240
```

`--kernel` is the one that matters: it boots the kernel and initramfs directly with
`console=ttyS0`, so a broken initramfs shows up as a named missing marker rather than a silent
hang. To poke at your addition interactively, boot with `debug` — `/init` drops to a shell at six
points.

## Limits

- **No `depmod`, no `ldconfig`** — the initramfs has neither. Static, self-contained, or it does not
  run.
- **Size costs boot time.** The image is read into RAM before anything executes; the shipped one is
  8.5 MiB compressed and 43.5 MiB unpacked.
- **Not for kernel modules** — use [`initramfs-add-modules`](initramfs-add-modules.md), which puts
  them in the version-specific directory and can pull them out of a bundle.
