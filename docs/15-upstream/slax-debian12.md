# The Debian flavour's build

`vendor/linux-live/Slax/debian12/`. Produces Slax 12.x from a **real, installed, running Debian 12**.

## `build` — 26 lines

```sh
sed -i -r 's/^LIVEKITNAME.*/LIVEKITNAME="slax"/' $THIS/../../config
sed -i -r 's/^NETWORK.*/NETWORK=true/' $THIS/../../config

. ./copy        # rootcopy -> /
. ./install     # apt upgrade + the package set
. ./cleanup     # strip locales, man, docs, caches
. ./copy        # again, so rootcopy wins over package defaults

SKIPINITRFS=true
cd ../../ ; . ./build

cd initramfs ; . ./initramfs_create
mv -f $INITRAMFS.img $LIVEKITDATA/$LIVEKITNAME/boot/initrfs.img
cp -vfR $THIS/bootfiles/* $LIVEKITDATA/$LIVEKITNAME/boot/
```

Two details worth noticing:

**`copy` runs twice.** `(cd rootcopy && cp --parents -afr * /)` — once before installing packages
and once after, so Slax's own files win over whatever a package's postinst wrote. That is how
`/root/.bashrc` keeps its `apt` wrappers and `getty@.service` keeps its `TTYVTDisallocate=no`.

**`SKIPINITRFS=true`, then the initramfs is built by hand afterwards.** It has to be: the initramfs
copies aufs modules out of the running system, so it must be built after the root filesystem is
final. The commented-out lines just above show the author wrestling with `aufs-dkms`.

## `install` — the package set

`apt update && apt upgrade --yes`, then a guard worth stealing:

```sh
CURRENT="$(ls -1 /boot)" ; ... ; NEW="$(ls -1 /boot)"
if [ "$CURRENT" != "$NEW" ]; then
   echo "It looks like your kernel has been upgraded."
   echo "You should reboot and restart the build process."
   exit
fi
```

If the upgrade pulled a new kernel, the running kernel no longer matches `/lib/modules`, so it stops.

Then one `apt-get install --no-install-recommends` of about 45 packages — `mc squashfs-tools
genisoimage zip psmisc net-tools alsa-utils xz-utils acpid mdadm smartmontools dosfstools htop rsync
ssh gpm ntfs-3g dnsmasq wget virt-what wpasupplicant connman` and so on — followed by deliberate
removals:

```sh
apt-get remove --yes vim* grub* debconf-i18n installation-report
apt-get remove --yes apparmor
```

`grub*` is removed because Slax boots with syslinux and has no use for it. `ln -sf bash /bin/sh`
makes `sh` bash rather than dash.

The tail re-extracts `acpi-support` with `dpkg -x` and copies it over `/`, working around something
in that package's maintainer scripts.

## `buildmods` — the other bundles, built in a union

This is the clever part, and it is nothing like the Slackware equivalent.

```sh
mount -t tmpfs tmpfs $EMPTY
mount -t aufs -o xino="$EMPTY/.xino",br="$EMPTY" none "$UNION"
for i in dev proc run sys; do mount --rbind /$i $UNION/$i; done
for i in $(ls -1 $SLAXMODSDIR | fgrep .sb); do
   mount -o loop $SLAXMODSDIR/$i /tmp/sb-mount-$i
   mount -o remount,add:0:"/tmp/sb-mount-$i" none "$UNION"
done
```

It reconstructs the live union on the build machine: already-built bundles as read-only branches,
with a writable branch on tmpfs. Then for each `modules/0N-*/` directory:

```sh
union_target_set /tmp/$i "$UNION"      # point the writable branch at /tmp/<bundle>
chroot $UNION /a/$i/build              # run that bundle's build script
chroot $UNION /cleanup
```

Everything the bundle's `build` writes lands in `/tmp/<bundle>` — which *is* the bundle. Each
bundle's script is a plain `apt install`:

```sh
# modules/04-apps/build
apt install --no-install-recommends --yes \
    galculator pcmanfm lxtask connman-gtk at-spi2-core scite xarchiver xdg-utils libgconf-2-4
```

Finally `remove_duplicites` compares every pair of bundles and deletes files that are byte-identical
and same-owner in both, so a library does not ship twice, and each `/tmp/<bundle>` is squashed:

```sh
mksquashfs /tmp/$i /tmp/$i.sb -comp xz -Xbcj x86 -b 1024k -always-use-fragments
```

## Why this matters to slax-kitchen

`buildmods` needs a running Debian, aufs, real mounts and root. slax-kitchen's `bundle.packages`
reaches the same result differently: unpack `01-core.sb`, `chroot`, install, and diff the tree. That
is deliberately *not* what upstream does, and the difference explains one of its subtleties — aufs
copy-up makes modified files physically present in the writable branch, whereas an offline diff must
detect modification explicitly. See [edit-bundles](../40-workflow/edit-bundles.md).

## Hardcoded paths

`buildmods` opens with `SLAXMODSDIR=/tmp/slax-data-540/slax/` — a literal PID-suffixed staging
directory from one particular build run. These scripts are the author's working tools, not a
turnkey product; expect to edit them.
