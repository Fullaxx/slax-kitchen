# The commands Slax adds

Eighteen helper commands that are not part of Debian or Slackware. They are copied into `01-core.sb`
from `vendor/linux-live/Slax/*/rootcopy/usr/bin/`, and they are what makes a booted Slax able to
modify itself.

This page is the **measured inventory** — which commands are actually present on which flavour. What
each one does internally is in [`15-upstream/runtime-tools.md`](../15-upstream/runtime-tools.md).

`kitchen` is deliberately a superset of this list, offline: everything here that operates on an image
has an equivalent that works on an unpacked ISO without booting it.

## What is where

| command | Debian | Slackware | |
|---|---|---|---|
| `slax` | ✓ | ✓ | `activate` / `deactivate` / `list` bundles on a live system |
| `savechanges` | ✓ | ✓ | freeze the writable layer into `99-changes-N.sb` |
| `dir2sb` | ✓ | ✓ | directory → bundle |
| `sb2dir` | ✓ | ✓ | bundle → directory |
| `rmsbdir` | ✓ | ✓ | remove a `sb2dir` working directory |
| `sb` | ✓ | ✓ | dispatcher — guesses which of the three you meant |
| `genslaxiso` | ✓ | ✗ *broken* | rebuild an ISO from the running system |
| `initramfs_pack` | ✓ | ✓ | directory → `initrfs.img` |
| `initramfs_unpack` | ✓ | ✓ | `initrfs.img` → directory |
| `pxe` | ✓ | ✗ *broken* | turn this machine into a PXE server |
| `slax-automount` | ✓ | ✓ | udev-driven `/media/<dev>` mounting |
| `slax-cleanup-automount` | ✓ | — | Debian only |
| `gtk-bookmarks-update` | ✓ | ✓ | keep the file manager's sidebar in step with `/media` |
| `txz2sb` | — | ✓ | Slackware `.txz` package → bundle |
| `showdeps` | — | ✓ | Slackware dependency lookup |
| `update-slackware-db` | — | ✓ | refresh the local package database |
| `update-slackware-libs` | — | ✓ | refresh the library index |
| `pxelinux-options` | — | ✓ | Slackware only |

Five of these are invisible to a filename search for "slax", which is how they get missed.

## The ones that matter

### `slax activate` — the only way to add a bundle without rebooting

```sh
slax activate 07-mytools.sb
slax list
slax deactivate 07-mytools.sb
```

It loop-mounts the bundle and splices it into the live aufs union at branch index 1 — the same
insertion `union_append_bundles` uses at boot, so an activated bundle outranks everything already
loaded.

**aufs only.** Under the overlayfs fallback the branch set is fixed at mount time and cannot be
extended, so these three commands simply cannot work. If `init_aufs` fell back, you will have seen
`Setting up union using overlayfs` at boot.

If the `.sb` is inside the union already, it is first copied to `/run/initramfs/memory/modules` —
you cannot loop-mount a file out of the filesystem you are mounting it into.

### `savechanges` — the live counterpart of building a bundle

```sh
savechanges                                  # → .../modules/99-changes-<next>.sb
savechanges /path/to/99-changes-1.sb
```

Packs `memory/changes` into a bundle numbered 99, so it outranks everything. It **keeps whiteouts**,
so deletions are saved too; it stages through tmpfs, so the changeset must fit in RAM; and it drops
the per-boot churn (`var/log`, `etc/fstab`, `root/.Xauthority`, …). Full exclusion list in
[`10-anatomy/union-and-persistence.md`](../10-anatomy/union-and-persistence.md).

### `genslaxiso` — and why `kitchen pack` exists

It is the reference implementation of rebuilding a Slax ISO, and it supports the obvious options:

```sh
genslaxiso -e 'chromium'                              # exclude a bundle
genslaxiso -e 'firmware|xorg|desktop|apps|chromium'   # core only
genslaxiso -r /path/to/rootcopy                       # bake in a rootcopy tree
```

Two limits explain why this toolkit is not just a wrapper around it:

1. **It only runs on a booted Slax.** It reads `/run/initramfs/memory`, so it cannot touch an ISO
   file on your desktop.
2. **It is broken on Slackware**, where it calls `genisoimage` and only `mkisofs` is installed.

`kitchen pack` is "genslaxiso, offline, plus UEFI and isohybrid".

### `dir2sb` / `sb2dir` / `sb`

```sh
sb2dir  05-chromium.sb   # unpack for editing
sb      <anything>       # the dispatcher figures out the direction
dir2sb  mydir 07-mine.sb # pack, with the canonical mksquashfs flags
```

`dir2sb` uses the same four flags as everything else — `-comp xz -b 1024K -Xbcj x86
-always-use-fragments` — which is why every bundle on every image has superblock flags `0x04e0`.

### `initramfs_pack` / `initramfs_unpack`

They are a **pair, and only work as a pair**: `initramfs_unpack` replaces the file with a tmpfs mount
holding the unpacked tree, and `initramfs_pack` repacks and unmounts it. Both need root and a
working `mount`, so neither runs in an unprivileged container.

```sh
initramfs_unpack /slax/boot/initrfs.img    # initrfs.img becomes a directory (tmpfs)
…edit…
initramfs_pack   /slax/boot/initrfs.img    # back to a file, tmpfs released
```

The pack command is the canonical one, CRC32 and all:

```sh
find . -print | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
```

Note there is no `sort` — see the reproducibility note in
[`10-anatomy/initramfs.md`](../10-anatomy/initramfs.md).

### `pxe`

Sets up dnsmasq plus `busybox httpd -p 7529` and serves the running system over the network. Works on
Debian; **fails on Slackware**, which has neither `busybox` in `$PATH` nor `dhclient`. The script is
byte-identical on both.

## In-bundle hooks

`DOC/bundle.txt` reserves four paths a bundle can provide:

| | |
|---|---|
| `/run/requires` | other bundles this one needs |
| `/run/activate.sh` | run on activation |
| `/run/deactivate.sh` | run on deactivation |
| `/run/startcmd.sh` | the command the launcher runs |

**No shipped bundle on any of the four images uses any of them.** The only `/run` content anywhere is
Slackware's `run/lock/pkgtools/ldconfig.lock`. The mechanism is documented and implemented but
untested by the official builds — treat it as experimental.

## The desktop helpers

Not in `01-core` but worth listing, since they are what a user actually presses. All in
`03-desktop.sb`, **identical on both flavours**:

| | |
|---|---|
| `fbappselect` | the app launcher — `Super` or `Alt+F2` |
| `fbliveapp` | on-demand app installer |
| `fbprintscreen` | `Print` |
| `fbscreensize` | resolution picker |
| `fbsetkb` | keyboard layout |
| `fblogout` | logout / reboot / poweroff dialog |

`fbrun`, `fbsetbg`, `fbsetroot` and `fbstartupnotify` alongside them are Fluxbox's own, not Slax's.

`Xdetect`, in `02-xorg.sb`, is the script both flavours' init systems call to start X.

**`fbliveapp` behaves differently per flavour.** Debian installs with apt:

```sh
INSTALL="apt install --yes vlc"
INSTALL="apt install --yes chromium chromium-sandbox"
```

Slackware downloads a prebuilt bundle from slax.org and activates it:

```sh
INSTALL="wget -c -O /tmp/05-chromium.sb 'https://slax.org/download-web-browser.php?b=slackware&v=…' \
         && slax activate /tmp/05-chromium.sb && rm /tmp/05-chromium.sb"
```

Do not generalise from one to the other — that download URL is Slackware-only, and the apt path is
Debian-only.
