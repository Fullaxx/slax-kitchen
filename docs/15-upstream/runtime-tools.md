# Scripts shipped *into* Slax

Distinct from the build scripts: these are copied into `01-core.sb` via `rootcopy/usr/bin/` and run
on the booted system. Sources are in `vendor/linux-live/Slax/*/rootcopy/usr/bin/` — identical on
both flavours except where noted.

| Script | What it does |
|---|---|
| `slax` | `activate` / `deactivate` / `list` bundles on a running system |
| `savechanges` | freeze the writable layer into `99-changes-N.sb` |
| `dir2sb`, `sb2dir`, `rmsbdir` | convert between directories and bundles |
| `sb` | dispatcher: guesses which of the above you meant |
| `txz2sb` | **Slackware only** — `.txz` package → bundle |
| `genslaxiso` | rebuild a Slax ISO from the running system |
| `initramfs_pack`, `initramfs_unpack` | open and close `initrfs.img` |
| `pxe` | turn the machine into a PXE server |
| `slax-automount`, `gtk-bookmarks-update` | udev-driven `/media/` mounting |
| `showdeps`, `update-slackware-db`, `update-slackware-libs` | Slackware only |

Five of these are invisible to a filename search for "slax", which is how they get missed.

## `slax` — aufs surgery on a live system

`activate` loop-mounts a bundle and splices it in:

```sh
mount -n -o loop,ro "$SB" "$TGT"
mount -t aufs -o remount,add:1:"$TGT=rr+wh" aufs /
```

`add:1:` again — the same index-1 insertion that gives higher-numbered bundles priority at boot. If
the `.sb` lives inside the union already it is first copied to `/run/initramfs/memory/modules`,
because you cannot loop-mount a file from the filesystem you are mounting it into.

`list` parses `/sys/fs/aufs/si_*/br[0-9]*` and maps each branch back to its loop file.

**All of this is aufs-only.** Under the overlayfs fallback these commands cannot work — branches
cannot be added to a mounted overlayfs.

## `savechanges` — the exclusion list

The interesting part is what it refuses to save:

```
^var/cache/ ^var/backups/ ^var/tmp/ ^var/log/ ^var/lib/apt/ ^var/lib/dhcp/ ^var/lib/systemd/
^etc/resolv[.]conf ^etc/mtab ^etc/fstab ^sbin/fsck[.]aufs
^root/[.]Xauthority ^root/[.]xsession-errors ^root/[.]fehbg
^boot/ ^dev/ ^mnt/ ^proc/ ^run/ ^sys/ ^tmp/
[.]wh[.] ...
```

`/etc/fstab` and `/etc/mtab` are excluded because `fstab_create()` generates them at every boot;
`/etc/resolv.conf` because it is per-network. slax-kitchen's `BUNDLE_EXCLUDE` mirrors this list for
exactly the same reasons.

It also stages into a **tmpfs** at `/tmp/changes$$` before squashing, so the entire changeset must
fit in RAM.

Default target is `99-changes-N.sb` — 99 so saved work outranks every shipped bundle.

## `genslaxiso` — the offline-rebuild reference

The closest thing upstream has to `kitchen pack`:

```sh
genisoimage -o - -quiet -v -J -R -D -A slax -V slax \
  -no-emul-boot -boot-info-table -boot-load-size 4 -input-charset utf-8 \
  -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
  -graft-points $GRAFT .
```

It builds the graft list by walking the live `/run/initramfs/memory/`, excluding `isolinux.bin`
and `isolinux.boot` (which the mastering tool regenerates) and `changes/`. Its own help text
documents the use cases:

```
# to create Slax iso without chromium.sb module:
genslaxiso -e 'chromium' slax_without_chromium.iso
# to create Slax text-mode core only:
genslaxiso -e 'firmware|xorg|desktop|apps|chromium' slax_textmode.iso
# to add user rootcopy directory:
genslaxiso -r /tmp/my-rootcopy slax_with_rootcopy.iso
```

Those three are, essentially, the `remove-bundle` and `rootcopy-overlay` recipes — done from inside
a booted Slax rather than offline.

**It only runs on a booted Slax**, because it reads `/run/initramfs/memory`. That is the gap
slax-kitchen fills. ⚠️ It is also **broken on the Slackware flavour**, where `genisoimage` is not
installed.

## `initramfs_pack` / `initramfs_unpack`

The authoritative recipe for `initrfs.img`, in two lines:

```sh
(cd "$IMG"; find . -print | cpio -o -H newc 2>/dev/null) | xz -T0 -f --extreme --check=crc32 >"$IMG.2"
(cd "$IMG"; xz -d | cpio -idv >/dev/null 2>&1) < "$IMG.2"
```

`unpack` mounts a **tmpfs** over the target directory first, so a large initramfs consumes RAM and
a plain `rm -rf` leaves a mount behind.

## `pxe`

Rebuilds an initramfs containing every non-wireless network driver, symlinks the kernel,
`pxelinux.0` and every bundle into `/var/state/dnsmasq/root`, writes a `PXEFILELIST`, then:

```sh
dnsmasq --enable-tftp --tftp-root=/var/state/dnsmasq/root \
        --dhcp-boot=pxelinux.0,"$IP",$IP --dhcp-range=$RANGE.2,$RANGE.250,infinite
busybox httpd -p 7529 -h /var/state/dnsmasq/root
```

With the comment: *"port 7529 (that are the numbers you type on your phone to write 'slax')"*.

## The `apt` wrapper

Not a script but worth recording — `rootcopy/root/.bashrc` defines shell functions:

```sh
apt-get() {
   if [ -e /var/cache/apt/pkgcache.bin ]; then /usr/bin/apt-get "$@"
   else /usr/bin/apt-get update; /usr/bin/apt-get "$@"; fi
}
```

So `apt install foo` on a freshly booted Slax runs `apt update` for you first, instead of failing
with "unable to locate package". They are exported functions, so they do not exist in a
non-interactive shell or inside a chroot — which is why slax-kitchen's `bundle.packages` runs
`apt-get update` explicitly.
