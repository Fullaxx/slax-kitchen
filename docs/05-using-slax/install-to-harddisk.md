# Installing to a hard disk

Slax is a live system, not an installed one — there is no installer and no "install to disk" option.
What you can do is put the `slax/` directory on a disk and boot it. It still runs as a live system,
just from faster media, with persistence.

## The straightforward way

Identical to the USB procedure, because the tooling does not distinguish them:

1. Pick or create a partition. **ext4 is strongly preferred over FAT32** — see
   [persistence-perch](persistence-perch.md) for why, it is the difference between unlimited
   persistence and a fixed 16 GB container.
2. Copy `slax/` from the ISO to the root of that partition.
3. Run `slax/boot/bootinst.sh` **as root** from the copied directory.

`bootinst.sh` writes `mbr.bin` over the first 440 bytes of the **whole disk** and marks the
partition active. On a disk that already boots something else, that replaces its MBR.

⚠️ It warns before doing this:

> *Partition /dev/sdXN seems to be located on a physical disk, which is already bootable. If you
> continue, your drive /dev/sdX will boot only Slax by default.*

Do not skip past that on a machine you care about. Use the chainload method below instead.

## Alongside an existing bootloader — safer

Leave the MBR alone and add an entry to the bootloader you already have. Copy `slax/` onto any
partition, do **not** run `bootinst.sh`, and instead:

### GRUB 2

`/etc/grub.d/40_custom`, then `update-grub`:

```
menuentry "Slax" {
    search --no-floppy --file --set=root /slax/boot/vmlinuz
    linux  /slax/boot/vmlinuz vga=normal rw printk.time=0 consoleblank=0 automount perchdir=resume
    initrd /slax/boot/initrfs.img
}
```

`search --file` finds whichever partition holds Slax, so this survives the disk being renumbered.

### syslinux / extlinux already on the disk

```
LABEL slax
    MENU LABEL Slax
    LINUX /slax/boot/vmlinuz
    INITRD /slax/boot/initrfs.img
    APPEND vga=normal rw printk.time=0 consoleblank=0 automount perchdir=resume
```

Use `LINUX`, not `KERNEL` — see the [memtest86plus](../50-cookbook/memtest86plus.md) page for why
that distinction bites.

## Booting an ISO file directly

No copying at all. Drop the `.iso` on any partition and point Slax at it:

```
linux  /slax/boot/vmlinuz from=/dev/sda3/isos/slax.iso ...
```

You still need the kernel and initramfs extracted somewhere the bootloader can read, but the
bundles stay inside the ISO, which the initramfs loop-mounts. Convenient for keeping several
versions around.

## What you do not get

**No installed system.** Slax always runs from bundles in a union. There is no `/` on the disk that
you can fsck or chroot into in the normal sense — the disk just holds `slax/` and, if you enabled
it, `slax/changes/`.

If you want a conventional Debian or Slackware install, install Debian or Slackware.

## Multiple machines, one disk layout

Because the initramfs *scans* for `/slax/` rather than hardcoding a device, the same disk works
moved between machines, and the same stick works whether it enumerates as `sdb` or `sdc`. Pin it
with `from=` only if you have more than one copy of Slax attached.
