# Writing to a USB stick

Three routes, and they are not equivalent. The one you want depends on whether you need persistence.

| route | works on a stock ISO | persistence | UEFI |
|---|---|---|---|
| **`bootinst`** — copy files, run the installer | ✅ yes | ✅ yes | ✅ yes |
| **`dd`** the ISO | ❌ **no** — needs the `isohybrid` recipe | ❌ never | only with `uefi-bootable` too |
| **manual `extlinux`** | ✅ yes | ✅ yes | manual |

**If you want persistence, use `bootinst`.** It is upstream's supported path and the only one that
produces a writable filesystem.

## Route 1 — `bootinst` (recommended)

```sh
# 1. a FAT32 partition (ext4 also works and is better — see below)
mkfs.vfat -F 32 -n SLAX /dev/sdX1

# 2. copy the tree
mount /dev/sdX1 /mnt
cp -a /path/to/iso-contents/slax /mnt/
umount /mnt

# 3. install the bootloader — as root, from the stick itself
mount /dev/sdX1 /mnt
/mnt/slax/boot/bootinst.sh
```

On Windows, run `slax\boot\bootinst.bat` instead — it is an sh/batch polyglot that escalates through
`runadmin.vbs` and refuses to touch the disk Windows booted from (`samedisk.vbs`).

### What `bootinst.sh` actually does

```sh
./extlinux.x$ARCH --install "$BOOT"                   # 1. write ldlinux.sys
dd bs=440 count=1 conv=notrunc if=mbr.bin of=$DEV     # 2. MBR boot code
fdisk … a … w                                          # 3. set the active flag
mv "EFI/Boot" "$BOOT/../../EFI/"                       # 4. hoist the EFI payload
```

Four things worth knowing before you run it:

- **Step 2 writes the MBR of the whole disk** and step 3 sets the boot flag. On a dedicated stick
  that is what you want. On a disk that already boots something else, **do not run this** — chainload
  instead, see [below](#hard-disks).
- **`$ARCH` comes from `uname -m` of the machine running the script**, not the Slax being installed.
  That is correct — the installer only writes to a filesystem.
- **Step 4 is `mv`, not `cp`.** After one run, `slax/boot/EFI/Boot` no longer exists. Re-running is
  not idempotent in the way you might assume, though it is harmless.
- It works around `noexec` and `showexec` mounts by remounting, then `chmod`, then finally copying
  `extlinux.x64` to `extlinux.exe`. Those fallbacks exist because the usual target is a
  desktop-mounted FAT stick.

### Pick the filesystem deliberately

This is the decision that matters most, and it is made at boot by a **live test** — create a file,
symlink it, toggle the executable bit — not by reading the filesystem type.

| | persistence becomes | limit |
|---|---|---|
| **ext4 / xfs / btrfs** | a plain `mount --bind` of the session directory | **none** |
| **FAT32** | a DynFileFS container with XFS inside | 16 GB default, `perchsize=` to raise |
| **NTFS** | same container path — **explicitly excluded** from the bind path even though ntfs3 passes the test | same |

**ext4 is the better choice** unless you also want the stick readable by Windows. No container, no
size ceiling, no `perchsize=` to get right the first time. SYSLINUX can install to ext4 fine.

Details: [persistence](../10-anatomy/union-and-persistence.md).

## Route 2 — `dd`, only after `isohybrid`

A stock Slax ISO `dd`'d to a stick **boots nothing**, on BIOS or UEFI. Its first 32 KiB are entirely
zero — no MBR boot code, no `0x55AA` signature, no partition table, no GPT:

```sh
head -c 32768 slax-64bit-debian-12.2.0.iso | tr -d '\0' | wc -c    # → 0
```

Apply the recipes first:

```sh
kitchen unpack isos/slax-64bit-debian-12.2.0.iso
kitchen apply isohybrid uefi-bootable
kitchen pack -o out/slax-usb.iso
```

Then:

```sh
dd if=out/slax-usb.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Note `of=/dev/sdX`, the **whole device**, not a partition — the image carries its own partition
table.

### The catch: no persistence

A `dd`'d image is an ISO9660 filesystem, and ISO9660 is read-only. There is nowhere for
`/slax/changes/` to be written, so `persistent_changes` reports:

```
* Persistent changes not writable or not used
```

Workarounds, in order of how well they work:

1. **Use `bootinst` instead.** If you want persistence, this route is the wrong one.
2. **Point `perchdir=` at a second device** — `perchdir=/dev/sdc1/slax/changes`.
3. **Add a second partition.** The hybrid GPT leaves room; create a data partition after the ISO
   image and point `perchdir=` at it. Fiddly, and it does not survive re-`dd`ing.

## Route 3 — manual `extlinux`

For when you want to know exactly what happened, or `bootinst.sh` will not run:

```sh
mount /dev/sdX1 /mnt
cp -a slax /mnt/
extlinux --install /mnt/slax/boot
dd bs=440 count=1 conv=notrunc if=/mnt/slax/boot/mbr.bin of=/dev/sdX
sfdisk --activate /dev/sdX 1
mkdir -p /mnt/EFI && cp -a /mnt/slax/boot/EFI/Boot /mnt/EFI/     # cp, not mv
umount /mnt
```

Use your host's `extlinux`, not the one in the ISO, and check the generation matches — see the
SYSLINUX warning in [edit-bootloader](edit-bootloader.md).

## Hard disks

Same as route 1, with one addition: **if the disk already boots an OS, do not let `bootinst` write
the MBR.** Copy `/slax/` on and add a GRUB entry instead:

```
menuentry "Slax" {
    search --file --set=root /slax/boot/vmlinuz
    linux  /slax/boot/vmlinuz vga=normal rw printk.time=0 consoleblank=0 perchdir=resume automount
    initrd /slax/boot/initrfs.img
}
```

Nothing is overwritten, and `find_data` locates `/slax/` on its own. On a machine with several disks,
set `from=/dev/sdaN/slax` explicitly so it does not spend its 45-second search budget probing.

You can even leave the ISO intact and boot it in place:

```
from=/dev/sda2/isos/slax-64bit-debian-12.2.0.iso
```

`find_data_try` notices the path names a file, loop-mounts it at `memory/iso`, and searches inside.
This is the least invasive option available.

## Verifying without hardware

The `dd` route is fully testable under QEMU — an isohybrid image is a valid disk image:

```sh
cp out/slax-usb.iso /tmp/usb.img
qemu-system-x86_64 -enable-kvm -m 2048 \
  -drive if=none,format=raw,id=u,file=/tmp/usb.img -device usb-storage,drive=u
```

Attaching it as `usb-storage` rather than `-hda` exercises the USB path in the initramfs, which is
where a missing driver would actually bite.

**Real-hardware verification is still worth doing**, because firmware varies in ways QEMU does not
reproduce — particularly which partition a UEFI implementation examines first. That, plus the actual
`dd` to a block device, is the part that cannot be done in a container: it is item **H-004** in the
host queue. See [host-handoff](host-handoff.md).
