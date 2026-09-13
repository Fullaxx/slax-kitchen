# `initramfs-add-modules` — reach hardware early boot cannot see

**Status: verified** — applied and booted on 64-bit Debian; also applied on 64-bit Slackware and
32-bit Debian, confirming both the merged-usr and per-arch cases.

```sh
kitchen apply initramfs-add-modules
```

## The problem it solves

`find_data` cannot mount what the kernel cannot see. If Slax boots on a machine but stops at

```
Cannot find slax data
```

a missing storage driver in the initramfs is the usual cause.

**The modules are almost certainly already on the ISO.** They are just not where early boot can
reach them:

| | 64-bit Debian |
|---|---|
| modules in `initrfs.img` | **301** |
| modules in `01-core.sb` | **4,766** |

`initramfs_create` selects by *directory* — `drivers/net/ethernet`, `drivers/usb`, `drivers/nvme`
and a handful more — so entire classes are absent from early boot while sitting unused in a bundle.

## Promoting from a bundle

```yaml
- verb: initramfs.modules
  from_bundle: 01-core
  subdir: kernel/extra
  modules: [nbd]
```

```
module pool: 01-core.sb (4,777 files)
initramfs: + lib/modules/6.1.38/kernel/extra/nbd.ko
module directory read from the tree: 6.1.38
repacked initrfs.img  8,872,472 -> 8,882,836 bytes (+10,364)
```

Nothing external is needed, and the module is **guaranteed to match the running kernel's ABI** —
it was built against the same kernel.

You can also supply your own files by omitting `from_bundle` and giving paths.

## ⚠️ Many obvious candidates are not modules at all

```
dm-mod   md-mod   raid0   raid1   raid10   raid456   virtio_blk   virtio_pci   virtio_scsi
```

None of these exists as a `.ko` on any Slax image — they are **compiled into the kernel**. They
need no promotion and cannot be found by this verb, which says so rather than failing obscurely:

```
initramfs.modules: dm-mod.ko not found in 01-core. Note many common storage drivers
(dm-mod, md-mod, raid1, virtio_*) are compiled INTO the Slax kernel, not shipped as
modules, so they need no promotion.
```

Worth promoting, and genuinely absent: `nbd`, `iscsi_tcp`, `libiscsi`, `nvme-fabrics`,
`dm-thin-pool`, `dm-integrity`, `dm-cache`.

## The two portability traps, both handled

**The module directory is version-specific.** Hardcoding it breaks half the targets:

| | |
|---|---|
| 64-bit | `lib/modules/6.1.38` |
| 32-bit | `lib/modules/6.1.38-smp` — the kernel carries `LOCALVERSION=-smp` |

The verb reads it from the tree and reports which it found.

**Bundle layout differs by flavour.** Debian's bundles are merged-usr (`lib -> usr/lib`), Slackware's
are not, so the module tree is at `usr/lib/modules` on one and `lib/modules` on the other. Extracting
only one silently yields an empty pool — which is exactly what happened during development, and why
the verb now tries both and fails loudly on an empty result.

## No `depmod` needed

`modprobe_everything()` finds modules by walking the tree:

```sh
find /lib/modules/ | fgrep .ko | egrep -v /drivers/net/ | sed -r "s:^.*/|[.]ko$::g" | xargs -n1 modprobe
```

Not by reading `modules.dep`. So a module dropped anywhere under `lib/modules/<release>/` is picked
up, and `subdir` only affects tidiness.

**Dependencies are your problem.** With no `modules.dep`, nothing resolves them — add a module's
dependencies explicitly, in an order that works.

## Compressed modules are refused

```
initramfs.modules: nbd.ko.xz is not a plain .ko. Decompress it first --
the shipped busybox modprobe cannot load compressed modules.
```

busybox 1.26.2's `modprobe` predates compressed-module support, which is why `initramfs_create`
decompresses `.ko.gz`/`.ko.xz` at build time. A future `initramfs-busybox` bump to ≥1.37.0 would
lift this.

## Verifying

```sh
kitchen pack && kitchen test out/*.iso --kernel --seconds 240
```

Then confirm the module is really in the shipped image:

```sh
xz -dc slax/boot/initrfs.img | cpio -it | grep extra/
```

On the booted system, `lsmod` shows what actually loaded — a module present but not loading usually
means an unmet dependency.
