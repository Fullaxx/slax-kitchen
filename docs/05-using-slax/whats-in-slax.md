# What is actually in Slax

Contents of the stock ISOs, by bundle. Load order is the numeric prefix and **higher wins**, so
anything in `05-chromium` beats `01-core`.

## Bundles

| Bundle | Debian 12.2.0 | Slackware 15.0.4 | Contents |
|---|---|---|---|
| `01-core` | 122 MiB | 119 MiB | the root filesystem: kernel modules, libc, shell, package manager, networking |
| `01-firmware` | 91 MiB | 80 MiB | **wireless and NIC firmware only** |
| `02-xorg` | 59 MiB | 15 MiB | X server, Mesa, fonts |
| `03-desktop` | 38 MiB | 19 MiB | Fluxbox, xfce4-panel, xlunch, themes, wallpaper |
| `04-apps` | 4 MiB | 4 MiB | file manager, text editor, calculator, archiver, ConnMan GUI |
| `05-chromium` | 79 MiB | 115 MiB | the web browser |
| `06-devel` | — | 81 MiB | **Slackware only**: gcc 13.2.0, headers, make |

`01-firmware` contains **no GPU firmware at all** — it is `firmware-iwlwifi`, `firmware-realtek`,
`firmware-atheros`, `b43` and friends. Removing it does not affect graphics.

On Slackware, `binutils` unexpectedly lives in `03-desktop`, not `06-devel`.

## Kernel

**6.1.38** on both flavours, 64-bit — and **`6.1.38-smp`** on 32-bit, where the `-smp` suffix means
the module directory is `/lib/modules/6.1.38-smp`.

It is custom-built, not a distro kernel, because it carries the out-of-tree **aufs** patch that Slax
depends on. Both flavours ship the *identical* kernel binary, built with Debian's gcc 12.2.0.

## What you get out of the box

**Debian flavour** (295 packages): systemd 252, apt, bash, busybox, ConnMan, wpasupplicant,
OpenSSH server, mc, nano, ntfs-3g, mdadm, squashfs-tools, genisoimage, alsa-utils, smartmontools.

**Slackware flavour** (227 packages in core, 423 across all bundles): sysvinit + elogind and **no
systemd anywhere**, pkgtools, slackpkg + slackpkg+, OpenSSH, dhcpcd, mc, plus the full
`squashfs-tools`, `cdrtools`, `syslinux` and `isohybrid` set — so Slackware Slax can rebuild its own
bundles and ISOs from the running system.

**Desktop**: Fluxbox as the window manager, xfce4-panel as the taskbar, xlunch as the launcher, feh
for wallpaper, compton for compositing, pcmanfm for files, SciTE for text, xarchiver, galculator,
lxtask.

## Slax's own tools

All in `01-core`, all small shell scripts worth reading:

| | |
|---|---|
| `slax activate\|deactivate\|list` | load or unload a bundle on a running system |
| `savechanges` | freeze your session into a permanent bundle |
| `dir2sb`, `sb2dir`, `rmsbdir`, `sb` | convert between directories and bundles |
| `txz2sb` | Slackware only: turn a `.txz` package into a bundle |
| `genslaxiso` | rebuild a Slax ISO from the running system |
| `initramfs_pack`, `initramfs_unpack` | open and close `initrfs.img` |
| `pxe` | turn the running machine into a PXE boot server |
| `fbliveapp` | launch an app, offering to install it if absent |

⚠️ `genslaxiso` **is broken on the Slackware flavour** — it calls `genisoimage`, which that build
does not ship (it has `mkisofs` instead). A symlink fixes it.

## The initramfs

8.9 MB compressed, and the same on all four ISOs apart from kernel modules. It contains BusyBox
**1.26.2 from 2017** plus eight static helper binaries, and **all of them are 32-bit i386 even on
the 64-bit ISOs** — one blob set serves both builds, which is why the x86_64 kernel needs
`CONFIG_IA32_EMULATION`.
