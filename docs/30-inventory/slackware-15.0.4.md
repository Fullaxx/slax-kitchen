# Slax 15.0.4 — the Slackware flavour

Slackware 15.0+, sysvinit, elogind, **zero systemd anywhere**. 423 packages across seven bundles,
455 MiB on 64-bit.

```
/etc/slax-version   Slax 15.0.4 64bit   (and correctly "32bit" on the 32-bit image)
released            2023-10-09
base                Slackware 15.0 + -current  ·  glibc 2.37
```

The version numbering tracks the base distribution, so there is no Debian-based 15.x and no
Slackware-based 12.x. They are parallel lines, not successive releases.

## Core versions

| | |
|---|---|
| init | **sysvinit 3.08** + `sysvinit-scripts` 15.1 + **elogind 252.9** |
| shell | bash 5.2.015 |
| libc | glibc 2.37 |
| coreutils | 9.4 |
| util-linux | 2.39.2 |
| package tools | pkgtools 15.1, slackpkg 15.0.10, **slackpkg+ 1.8.0** |
| network | `dhcpcd` 10.0.3 + ConnMan 1.36 (`_SBo`) |
| squashfs-tools | 4.6.1 |
| xz | 5.4.4 |
| ISO tool | **`mkisofs`** (cdrtools 3.02a09) — *not* `genisoimage` |
| syslinux | **4.07** — while the boot payload is 6.03 |
| busybox | **none in `$PATH`** |

Two of those rows are bugs waiting to happen, and both are in
[`known-upstream-bugs.md`](known-upstream-bugs.md):

- `genslaxiso` calls `genisoimage`, which is not installed — only `mkisofs` is. **Self-hosted ISO
  rebuild is broken as shipped**, and the fix is one word.
- The in-image `syslinux`/`extlinux` are **4.07** while every loader on the ISO is 6.03. Running them
  against a Slax stick installs a mismatched generation, and the COM32 modules will not load.

## No busybox, except two symlinks

```
/usr/bin/vi       -> /run/initramfs/bin/busybox
/usr/bin/nslookup -> /run/initramfs/bin/busybox
```

There is no `/bin/busybox` or `/usr/bin/busybox` on Slackware Slax. Those two symlinks reach back into
the initramfs, which `change_root` deliberately preserved at `/run/initramfs`. Elegant — and it means
**Slax's `vi` is busybox vi from 2017**, and that anything calling `busybox <applet>` by name fails.

`/usr/bin/pxe` does exactly that (`busybox httpd -p 7529 …`). The script is **byte-identical on both
flavours**; it simply happens to work on Debian, which ships a real busybox package, and not here.
The same script also calls `dhclient`, which Slackware does not have — it uses `dhcpcd`.

## Packages

423 across seven bundles. Unlike Debian, Slackware records **one file per package** in
`var/lib/pkgtools/packages/`, so bundles merge in the union instead of shadowing each other — which
is why the manifest is per-bundle:

| bundle | packages |
|---|---|
| `01-core` | 227 |
| `01-firmware` | **1** |
| `02-xorg` | 118 |
| `03-desktop` | 43 |
| `04-apps` | 15 |
| `05-chromium` | 4 |
| `06-devel` | 15 |

Full list: [`manifests/slackware-64bit-15.0.4.packages.tsv`](manifests/slackware-64bit-15.0.4.packages.tsv).

### `01-firmware` is one package

```
kernel-firmware-from-debian-nover-noarch-1
```

Slackware repackages Debian's firmware wholesale. Like Debian's, it is **wireless and NIC only — no
GPU firmware.**

### `06-devel` — the bundle Debian does not have

gcc 13.2.0, g++, make 4.4.1, flex, nasm, yasm, guile, elfutils, `kernel-headers 6.1.56`,
`xorgproto`, pkg-config — 15 packages, 25,732 inodes, 80.9 MiB.

Note the headers are **6.1.56** while the running kernel is 6.1.38. Fine for most builds, but not a
match; anything compiling against the running kernel should be aware.

### `_SBo` packages

Much of `04-apps` and parts of `03-desktop` come from SlackBuilds rather than the official tree —
`connman`, `connman-gtk`, `pcmanfm`, `lxtask`, `galculator`, `xarchiver`, `compton`. `05-chromium`'s
browser is `chromium-117.0.5938.149-x86_64-1alien`, an AlienBOB build.

`03-desktop` also carries **binutils 2.41**, which is an unexpected place for it and is why that
bundle is larger than `02-xorg`.

## Startup

No systemd, so the chain is scripts all the way:

```
/etc/inittab → rc.S → rc.M → /etc/rc.d/rc3.d/Slax.03.startup
```

```sh
#!/bin/bash
# This script gets executed at the end of rc.M
if grep -q -w text /proc/cmdline; then
   exit
fi
echo "Starting X ..."
/bin/su --login -c "/usr/bin/Xdetect -- :0 vt7 -ac -nolisten tcp" >/dev/null 2>/dev/null &
```

`grep -q -w text` is the **only word-anchored boot-parameter test in the whole system**; everything
else uses loose substring matching. The `su` line is byte-identical to the one in Debian's
`xorg.service`.

## Shutdown

Without systemd, `rc.6` carries the pivot back into the initramfs by hand, including a stub
`/sbin/init` and a `telinit u` to detach PID 1 from the old root. It is the more fragile of the two
paths — a customization that replaces `rc.6` can silently lose clean shutdown. Fully annotated in
[`10-anatomy/shutdown.md`](../10-anatomy/shutdown.md).

## Credentials

```
root / toor        printed in /etc/issue, in yellow, line 18
guest  uid 1000    for Chromium
```

Identical to Debian, including the ANSI colour codes in the banner.

## Adding packages — deferred

Slackware has no official dependency-resolving package manager, and the shipped configuration does
not work today:

```
/etc/slackpkg/mirrors           https://mirrors.slackware.com/slackware/slackware64-current/
/etc/slackpkg/slackpkgplus.conf slackpkgplus → https://slakfinder.org/slackpkg+15/
                                slackonly    → https://slackonly.com/pub/packages/15.0-x86_64/
```

Two problems, both measured:

- **`slackonly.com` is NXDOMAIN.** Hardcoded in the shipped `slackpkgplus.conf`, so any `slackpkg
  update` hits a dead host.
- **The mirror points at `-current`**, which has moved far past the 2023 snapshot the image was built
  from. `slackpkg` prompts for confirmation on the version mismatch, declines its own prompt under
  `-batch=on`, and then **exits 0 having installed nothing**.

`kitchen`'s `bundle.packages` therefore supports Debian fully and treats Slackware as deferred. What
does work is installing from **pinned `.txz` files** — which is what `txz2sb` does on a running
system, and what a future `bundle-from-txz` recipe will do offline. Details in
[`50-cookbook/add-packages.md`](../50-cookbook/add-packages.md) and issues 8 and 9 in
[`known-upstream-bugs.md`](known-upstream-bugs.md).

## Slackware-only tooling

`txz2sb`, `showdeps`, `update-slackware-db`, `update-slackware-libs`, `pxelinux-options` — see
[`slax-tooling.md`](slax-tooling.md).
