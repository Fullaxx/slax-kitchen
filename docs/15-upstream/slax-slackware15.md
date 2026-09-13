# The Slackware flavour's build

`vendor/linux-live/Slax/slackware15/`. Produces Slax 15.0.x, and works **nothing like** the Debian
side.

## The core difference

Debian's `buildmods` chroots into a union and runs `apt` per bundle. Slackware's `modbuild` reads a
**declarative package list** per bundle and installs with `installpkg -root`. No chroot, no package
manager resolving anything — because Slackware's package tools do not resolve dependencies at all.

## `build` — 29 lines

```sh
bash ./modbuild keep

rm -f /vmlinuz
cat /boot/vmlinuz-6* > /vmlinuz

sed -i -r 's/^LIVEKITNAME.*/LIVEKITNAME="slax"/' $THIS/../../config
sed -i -r 's/^NETWORK.*/NETWORK=true/' $THIS/../../config

SKIPINITRFS=true
SKIPCOREMOD=true          # <-- 01-core comes from modbuild, not from /
cd ../../ ; . ./build

cd initramfs ; . ./initramfs_create
mv -f $INITRAMFS.img $LIVEKITDATA/$LIVEKITNAME/boot/initrfs.img
cp -vfR $THIS/bootfiles/* $LIVEKITDATA/$LIVEKITNAME/boot/
mv /tmp/*.sb $LIVEKITDATA/$LIVEKITNAME/modules
```

**`SKIPCOREMOD=true` is the tell.** On Debian, `01-core.sb` is the build machine's own root
filesystem. Here every bundle including `01-core` is assembled from packages, so `build`'s own
core-bundle step is switched off entirely.

`cat /boot/vmlinuz-6* > /vmlinuz` copies in the kernel — which, as
[aufs-kernel](aufs-kernel.md) shows, is a Debian-built binary shared with the other flavour.

## `modbuild` — hardcoded to the author's machine

```sh
SLAXVERSION="15.0.4"
SLACKWARE=/mnt/z/slackware-current/slackware64/
SLAX=/mnt/z/slackware-slax/64bit
FTP=rsync://rsync@ftp.linux.cz:/pub/linux/slackware/slackware64-current/slackware64/
export SLAXVERSION="Slax $SLAXVERSION 64bit"
```

Local mirror paths, a version string baked into a variable, and an `rsync` line commented out just
below. These are working tools, not a product — reproducing a Slackware Slax build means recreating
that mirror layout first.

Note the mirror is **`slackware64-current`**, not a release tree. That is why the shipped
`/etc/os-release` reads `Slackware 15.0 x86_64 (post 15.0 -current)` and why installing anything
from the stock mirror config today pulls packages years newer than the base — see
[add-packages](../50-cookbook/add-packages.md).

## `pkglist` — the declarative bit

Each `modules/0N-*/` holds a `pkglist` and usually a `doinst.sh`:

```
slackware:adwaita-icon-theme
slackware:dbus-glib
slackware:libexif
slackware:gnutls
slackware:p11-kit

slax:libfm
slax:libfm-extra
```

`<tree>:<package>` — `slackware:` from the stock tree, `slax:` from Tomáš's own package set. Each
line becomes `installpkg -root "$UNION"`. `doinst.sh` runs afterwards for post-install fixups, and
`modbuild` finishes by running the `update-*` cache commands and `ldconfig` inside the union.

Compare a Debian bundle's `build`, which is an `apt install` line and lets apt work out
dependencies. Here **every dependency is listed by hand** — which is also why `packages: [foo]` in a
slax-kitchen recipe cannot be trusted on this flavour.

## `reduce` — size reduction

Strips whiteouts, empties `boot/` and `dev/`, removes docs and man pages, moves development files
into `06-devel`, and recompresses zip archives with `gzip0.php`. This is why `06-devel.sb` exists on
Slackware and not on Debian: the split is deliberate, done by moving files out of the other bundles.

One consequence visible in the shipped ISO: **`binutils` ends up in `03-desktop`, not `06-devel`**.

## `helpers/`

`fw.php`, `gzip0.php`, `syslinux.php` and `waitforesc` — yes, PHP in a distribution build. `fw.php`
generates the firmware list; `gzip0.php` recompresses; `syslinux.php` manipulates boot config.

## Known breakage in the shipped result

Three Debian-isms that were never ported, all confirmed in the 15.0.4 ISO:

- **`genslaxiso` calls `genisoimage`**, which this flavour does not ship — it has `mkisofs`. Self-hosted ISO rebuilding fails out of the box.
- **`pxe` calls `dhclient`** (Slackware uses `dhcpcd`) and `busybox httpd` (no busybox in this rootfs — only in the initramfs).
- **`savechanges` greps `/etc/systemd/system/`** for its automount exclusions, on a flavour with no systemd at all, so that exclusion silently never fires.

See [known-upstream-bugs](../30-inventory/known-upstream-bugs.md).
