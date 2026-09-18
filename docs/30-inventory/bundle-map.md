# What is in each bundle

Six bundles on Debian, seven on Slackware. The numbering is the load order and
[higher wins](../10-anatomy/bundles-squashfs.md).

## At a glance

| | Debian 12.2.0 | | Slackware 15.0.4 | |
|---|---|---|---|---|
| | size | inodes | size | inodes |
| `01-core` | 122.3 MiB | 18,666 | 118.6 MiB | 24,321 |
| `01-firmware` | 91.1 MiB | 930 | 79.9 MiB | 783 |
| `02-xorg` | 58.9 MiB | 2,192 | 14.5 MiB | 2,454 |
| `03-desktop` | 37.7 MiB | 7,887 | 19.4 MiB | 7,989 |
| `04-apps` | 4.2 MiB | 1,791 | 4.4 MiB | 1,342 |
| `05-chromium` | 79.1 MiB | 277 | 114.6 MiB | 331 |
| `06-devel` | — | — | 80.9 MiB | 25,732 |

`05-chromium` carries ~100 MiB in ~300 inodes because Chromium ships as a handful of very large
files — which is also why removing it reclaims so much for so little structural change.

The 32-bit figures are in [`manifests/`](manifests/); the pattern holds, except that 32-bit Debian's
`02-xorg` and `05-chromium` are both *larger* than the 64-bit ones.

## Bundle by bundle

### `01-core` — the operating system

A complete base install. `bash`, `coreutils`, `util-linux`, the package manager, `mc`, `nano`,
networking, and the Slax helper commands. **It is a bootable system on its own** — `noload` everything
else and you get a console.

| | Debian | Slackware |
|---|---|---|
| packages | 295 (286 installed, 9 removed-but-configured) | 227 |
| init | systemd 252.17 | sysvinit + elogind |
| network | ConnMan 1.41 + `dhclient` | `rc.inet1` + `dhcpcd` |
| shell | bash 5.2.15 | bash |
| libc | 2.36-9+deb12u3 | |
| busybox | `/usr/bin/busybox` 1.35.0 (a real package) | **none** — only `vi` and `nslookup`, symlinked to `/run/initramfs/bin/busybox` |

That last row is worth dwelling on. **Slackware Slax has no `busybox` command**, and its `vi` reaches
back into the preserved initramfs:

```
/usr/bin/vi       -> /run/initramfs/bin/busybox
/usr/bin/nslookup -> /run/initramfs/bin/busybox
```

An elegant way to avoid shipping a second copy — and the reason `pxe` fails on Slackware, since it
calls `busybox httpd` by name. See [`known-upstream-bugs.md`](known-upstream-bugs.md).

The two flavours' `01-core` are also shaped differently: Debian's contains no runtime directories at
all, Slackware's is a full FHS tree with 7,323 device nodes. That asymmetry is explained in
[`10-anatomy/bundles-squashfs.md`](../10-anatomy/bundles-squashfs.md) and it is the single most
important thing to know before modifying a bundle.

### `01-firmware` — wireless only

Same number, loaded after `01-core` because `sort -n` falls back to the rest of the string.

**Debian** — 12 packages, every one of them wireless or NIC:

```
b43-fwcutter  firmware-atheros  firmware-b43-installer  firmware-bnx2
firmware-brcm80211  firmware-cavium  firmware-ipw2x00  firmware-iwlwifi
firmware-libertas  firmware-realtek  firmware-ti-connectivity  firmware-zd1211
```

**Slackware** — one package, and its name is the whole story:

```
kernel-firmware-from-debian-nover-noarch-1
```

Slackware repackages Debian's firmware wholesale.

**There is no GPU firmware in either.** No `firmware-amd-graphics`, no `firmware-misc-nonfree`, no
`i915` blobs. Modern AMD and Intel graphics will fall back to a basic mode, and some will not start X
at all. This is the single most impactful gap in the shipped software, and `firmware-refresh` is the
recipe that closes it.

### `02-xorg` — the X server

Xorg, drivers, fonts, `xterm`, and `Xdetect` — the script both flavours use to start X.

The size difference is the largest of any bundle: **58.9 MiB on Debian against 14.5 MiB on
Slackware**, for almost the same inode count. Debian ships far more input and video drivers and a
much larger font set.

### `03-desktop` — Fluxbox and the launcher

`fluxbox`, `xfce4-panel`, `xlunch`, and Slax's own `fb*` helpers — `fbappselect`, `fbliveapp`,
`fbprintscreen`, `fbscreensize`, `fbsetkb`, `fblogout`.

Slackware's copy **also carries `binutils 2.41`**, which is an odd place for it and is why the
Slackware `03-desktop` is bigger than its `02-xorg`.

### `04-apps` — the small stuff

`pcmanfm`, `connman-gtk`, a calculator, an archiver, a text editor. 4 MiB on both flavours; the
cheapest bundle to replace with your own.

Slackware's 15 packages are mostly `_SBo` — built from SlackBuilds rather than from the official
tree.

### `05-chromium` — the browser

| | |
|---|---|
| Debian | `chromium` + `chromium-sandbox`, installed with `apt` at build time |
| Slackware | `chromium-117.0.5938.149-x86_64-1alien` plus `icu4c`, `mozilla-nss`, `sqlite` |

Chromium refuses to run as root, which is why both flavours ship a `guest` user (uid 1000) purely to
run it.

**This is the first bundle to drop** if you want a smaller image: −79 MiB on Debian, −115 MiB on
Slackware, and nothing else *in the stock image* depends on it.

⚠ That last clause matters if you are adding a browser rather than removing one. `05-chromium` is
not only Chromium: it carries `libnss3`, `libnspr4`, `libopus0`, `libvorbis0a`, `libvorbisenc2`,
`libflac12`, `libpulse0`, `libsndfile1`, `libwebpmux3`, `libwoff1`, `libmp3lame0`, `libmpg123-0`
and `libopenh264-7` — effectively the shared browser runtime. Drop it and anything else with a
rendering engine has to ship its own copies. See
[`remove-bundle`](../50-cookbook/remove-bundle.md).

### `06-devel` — Slackware only

gcc 13.2.0, g++, make, flex, nasm, yasm, `kernel-headers 6.1.56`, `xorgproto`, pkg-config — 15
packages and 25,732 inodes for 80.9 MiB. Debian has no equivalent; if you want a compiler there you
build a bundle.

Note `kernel-headers` is **6.1.56**, while the running kernel is 6.1.38. Harmless for most builds,
but not a match.

## The cumulative package database

This is the most surprising thing in the layout, and it matters if you build bundles.

**Every Debian bundle carries its own `var/lib/dpkg/status`, and each one is cumulative:**

| bundle | packages in its `status` |
|---|---|
| `01-core` | 295 |
| `01-firmware` | 307 |
| `02-xorg` | 424 |
| `03-desktop` | 535 |
| `04-apps` | 575 |
| `05-chromium` | **600** |

Because higher-numbered bundles win, a fully loaded system sees `05-chromium`'s copy — all 600
packages. This falls straight out of the Debian build method: each bundle is built by chrooting into
the union of everything below it and running `apt`, so its package database naturally reflects the
whole stack.

Two consequences:

1. **`noload=` stays consistent going down.** Drop `05-chromium` and you get `04-apps`' database,
   which correctly does not list Chromium.
2. **It is inconsistent going sideways.** `noload=01-firmware` leaves `02-xorg`'s database, which
   *does* list the twelve firmware packages. dpkg will believe they are installed.

And the one that matters for customization:

> **A new high-numbered bundle must not carry a `dpkg/status` of its own**, or it shadows the
> 600-package database with whatever smaller one it built and dpkg forgets most of the system.

This page used to claim `bundle.packages` "gets this right by construction" by stacking the whole
image in the chroot. That is half a fix, and we shipped the other half as a bug: it makes *one*
add-on bundle correct, and still breaks the moment there are two, because whichever lands higher
replaces the other's database. Our own `add-packages` did not even manage the first half — it
built from `01-core` alone and put a 299-package `status` above `05-chromium`'s 600.

`kitchen` no longer ships `var/lib/dpkg/status` in a bundle at all. Each add-on carries
`var/lib/slax-kitchen/dpkg-status.d/<bundle>` — only the stanzas it added or changed — and
`kitchen pack` merges base plus fragments into a generated `98-dpkg-db.sb`. A *directory* of
fragments composes the way Slackware's database already does, which is the whole trick. See
[composing bundles](../40-workflow/composing-bundles.md).

Slackware has no equivalent problem — `var/lib/pkgtools/packages/` holds one file per package, so
bundles merge in the union rather than shadowing each other. That is why the Slackware manifest is
per-bundle and the Debian one is not.

## Reading a bundle yourself

```sh
# offsets: 01-core is always at 137216 on every image
unsquashfs -o 137216 -ll isos/slax-64bit-debian-12.2.0.iso | head

# find the rest
xorriso -indev isos/slax-64bit-debian-12.2.0.iso -find /slax/modules -exec report_lba --

# extract one file
unsquashfs -o 137216 -d /tmp/x isos/slax-64bit-debian-12.2.0.iso etc/slax-version
```

Nothing is extracted and nothing is mounted; `unsquashfs -o <offset>` reads the image in place in
about 20 ms.
