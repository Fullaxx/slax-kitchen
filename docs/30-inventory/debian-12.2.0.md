# Slax 12.2.0 — the Debian flavour

Debian 12 (bookworm), systemd, ConnMan, Fluxbox. **600 packages** in the fully loaded system, 416 MiB
on 64-bit.

```
/etc/slax-version   Slax 12.2.0 64bit
released            2023-10-09
base                Debian 12 bookworm  ·  libc 2.36-9+deb12u3
```

On the **32-bit** image `/etc/slax-version` also says `64bit` — byte-identical to the 64-bit file.
Anything keying off it to detect word size will be wrong; use an ELF probe instead. `kitchen probe`
does. See [`known-upstream-bugs.md`](known-upstream-bugs.md).

## Core versions

| | |
|---|---|
| init | systemd 252.17-1~deb12u1 (+ `systemd-sysv`, `udev`) |
| shell | bash 5.2.15-2+b2 |
| libc | 2.36-9+deb12u3 |
| coreutils | 9.1-1 |
| util-linux | 2.38.1-5+b1 |
| package manager | apt 2.6.1, dpkg 1.21.22 |
| network | ConnMan 1.41-3, `dhclient`, dnsmasq |
| python | 3.11.2 |
| busybox | 1.35.0-4+b3 — a real `/usr/bin/busybox` |
| squashfs-tools | 4.5.1-1 |
| xz-utils | 5.4.1-0.2 |

`squashfs-tools`, `xz-utils`, `dosfstools` and `mtools` are all present, which is what makes
`genslaxiso` work from a booted Debian Slax — and what makes it *not* work on Slackware.

## Packages

295 entries in `01-core`'s dpkg database, of which **286 are installed and 9 are
removed-but-configured**:

```
apparmor                    deinstall ok config-files
grub-common                 deinstall ok config-files
grub-pc                     deinstall ok config-files
grub2-common                deinstall ok config-files
installation-report         deinstall ok config-files
linux-image-6.1.0-13-amd64  deinstall ok config-files
linux-image-amd64           deinstall ok config-files
vim-common                  deinstall ok config-files
vim-tiny                    deinstall ok config-files
```

That list is the build's own changelog: GRUB removed in favour of SYSLINUX, the stock kernel removed
in favour of the custom aufs one, AppArmor removed, and vim traded for nano.

The **32-bit package name set is identical except for those kernel lines** — `linux-image-6.1.38-smp`,
`linux-image-6.1.0-13-686-pae`, `linux-image-686-pae`. 295 rows each, 48 `all` and 247 `amd64`/`i386`.

Full list: [`manifests/debian-64bit-12.2.0.packages.tsv`](manifests/debian-64bit-12.2.0.packages.tsv).

## How the bundles stack

Each bundle is built by chrooting into the aufs union of everything below it and running `apt`. That
gives every bundle a **cumulative** `var/lib/dpkg/status` — 295 → 307 → 424 → 535 → 575 → 600. See
[`bundle-map.md`](bundle-map.md); it is the fact most likely to catch you out when building a bundle
of your own.

Contents added per bundle:

| | adds |
|---|---|
| `01-firmware` | 91 MiB: 713 firmware files, 211 MiB unpacked. 12 packages: ten Debian firmware packages for wireless, Bluetooth and Ethernet (`bnx2` and `cavium` are wired), plus `firmware-b43-installer` and `b43-fwcutter`, which during Slax's build cut 155 Broadcom b43 firmware files out of Broadcom's `wl` driver and listed them in `b43/firmware-b43-installer.catalog`. The packages' `copyright` files were removed; `usr/lib/firmware/ipw2x00.LICENSE` is the one license text left, and none came with the b43 files. [What `firmware-refresh` adds on top](../50-cookbook/firmware-refresh.md#the-stock-firmware-it-builds-on) |
| `02-xorg` | Xorg, drivers, fonts, `xterm`, `Xdetect` |
| `03-desktop` | fluxbox, xfce4-panel, xlunch, the `fb*` helpers |
| `04-apps` | pcmanfm, connman-gtk, galculator, xarchiver, scite, lxtask + 33 libraries |
| `05-chromium` | `chromium`, `chromium-common`, `chromium-sandbox` + 22 media/crypto libraries |

## Startup

```
default.target → graphical.target → display-manager.service
                                    └─ symlink to /lib/systemd/system/xorg.service
```

`xorg.service` is hand-written, four lines of substance:

```ini
[Unit]
Description=X-Window
ConditionKernelCommandLine=!text
After=systemd-user-sessions.service

[Service]
ExecStart=/bin/su --login -c "/usr/bin/Xdetect -- :0 vt7 -ac -nolisten tcp"
```

`ConditionKernelCommandLine=!text` is how the `text` boot parameter works. `Xdetect` then picks a
driver and calls `startx`, which runs `/root/.xinitrc` — `startfluxbox`.

The `ExecStart` line is **byte-identical to Slackware's**, which is why desktop-level customization
works the same on both flavours even though nothing else about init does.

## Networking

**ConnMan**, not NetworkManager, not `ifupdown`. `connmand` runs as a service and `connman-gtk`
(in `04-apps`) is the tray UI. There is a stray `/etc/NetworkManager/dispatcher.d/ntpsec-ntpdate`
left behind in `01-core` by a package, but no NetworkManager.

## Credentials

```
root / toor        printed in /etc/issue
guest  uid 1000    exists because Chromium refuses to run as root
```

## Shutdown

systemd looks for an executable `/run/initramfs/shutdown` at the end of shutdown and pivots into it
automatically — no unit file, no configuration. Slackware has to do this by hand; see
[`10-anatomy/shutdown.md`](../10-anatomy/shutdown.md).

## Adding packages

The only fully supported package-addition path in `kitchen`, because apt is a real package manager
with working mirrors:

```yaml
- verb: bundle.packages
  bundle: 07-mytools
  packages: [tmux, ncdu, rsync]
```

It unpacks the existing bundles into a stacked tree, chroots in, runs `apt-get install`, diffs the
result, and packs the delta as a new high-numbered bundle. Requires real `chroot` — see
[`40-workflow/container-vs-host.md`](../40-workflow/container-vs-host.md) and
[`50-cookbook/add-packages.md`](../50-cookbook/add-packages.md).

**Bookworm is oldstable.** Pin `snapshot.debian.org` if you need a reproducible result, and expect
that unpinned installs drift from the 2023 snapshot the rest of the image was built from.
