# What is in `vendor/linux-live`

743 files at pin `9825e93`. Two projects share the tree: **Linux Live Kit** at the top level, and
**Slax** under `Slax/`.

```
linux-live/
├── build                     Linux Live Kit master build script
├── config                    build-time settings (LIVEKITNAME, VMLINUZ, MKMOD, NETWORK…)
├── livekitlib                49 shell functions -- shared by the build AND the initramfs
├── bootinfo.txt              becomes readme.txt on the ISO
├── README                    "turn your existing installed Linux into a Live Kit"
│
├── initramfs/
│   ├── initramfs_create      assembles and compresses initrfs.img
│   ├── init                  what the kernel executes  (ships verbatim)
│   ├── shutdown              clean unmount + CD eject  (ships verbatim)
│   └── static/               9 prebuilt static binaries, committed as blobs (8 in 12.2.0)
│
├── bootfiles/                syslinux 6.03 family + mbr.bin + bootinst.{sh,bat}
│
├── tools/
│   ├── isolinux.bin.update   re-patch isolinux.bin when LIVEKITNAME changes
│   ├── bootlogo.update       rebuild the boot splash
│   └── mkfs.xfs.custom.c     source for one of the static binaries
│
├── DOC/                      LICENSE, GNU_GPL, bundle.txt, terminology.txt,
│                             boot_parameters.txt, supported_fs.txt, sources.txt
│
└── Slax/
    ├── debian12/
    │   ├── build             the Slax-Debian driver
    │   ├── copy              rootcopy -> /
    │   ├── install           the apt package set
    │   ├── cleanup           strip locales, man, docs, caches
    │   ├── buildmods         builds 01-firmware … 05-chromium in an aufs union
    │   ├── bootfiles/        Slax-branded boot menu, logo, help
    │   ├── rootcopy/         files baked into 01-core (incl. /usr/bin/slax et al)
    │   ├── modules/0N-*/     one directory per bundle, each with a `build` script
    │   └── aufs-kernel-compile/   the custom kernel
    │
    └── slackware15/
        ├── build             the Slax-Slackware driver
        ├── modbuild          builds every bundle from pkglists
        ├── reduce            size reduction
        ├── helpers/          fw.php, gzip0.php, syslinux.php, waitforesc
        └── modules/0N-*/     pkglist + doinst.sh per bundle
```

## The one thing to understand first

**Linux Live Kit does not build a distribution. It converts one.** From upstream's `README`:

> Use this set of scripts to turn your existing preinstalled Linux distribution into a Live Kit …
> only root can continue, because only root can read all files from your system

So `build` runs **on a real, installed, running system** and squashes it into `01-core.sb`. There is
no chroot, no debootstrap and no container anywhere in the official build. That single fact explains
most of what looks odd about the scripts.

## Two structural asymmetries

**The Debian and Slackware flavours build bundles completely differently.** Debian's `buildmods`
sets up an aufs union over tmpfs and `chroot`s in to run a per-bundle `build` script that calls
`apt`. Slackware's `modbuild` reads a declarative `pkglist` per bundle and runs
`installpkg -root`. Neither shares code with the other.

**`livekitlib` is used twice, for different purposes.** It is sourced by `build` at build time
*and* copied into the initramfs as `/lib/livekitlib` to be sourced by `/init` at boot. A handful of
functions — `create_bundle`, `allow_only_root` — only ever run at build time; the rest only ever run
at boot.

## What is not here

- **No ISO is built by `build`.** It writes a shell script to `/tmp/gen_slax_iso.sh` for you to run.
- **No source for `initramfs/static/`.** Nine binaries are committed as blobs; `DOC/sources.txt`
  points at a Slax 7.x URL for busybox and ntfs-3g only.
- **No CI, no tests, no release automation.**
