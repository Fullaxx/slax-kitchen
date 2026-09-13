# BIOS + USB stick or hard disk

Upstream's supported route for removable media, and the only one that gives you persistence. It is
**not** produced by `dd`-ing the ISO — it is produced by copying files and running an installer.

## Preparing the medium

```
1. partition + format FAT32   (or ext4 / any filesystem SYSLINUX can write to)
2. copy /slax/ from the ISO onto it
3. run  slax/boot/bootinst.sh    (Linux)
        slax\boot\bootinst.bat   (Windows)
```

`bootinst.sh` does four things, in this order:

```sh
./extlinux.x$ARCH --install "$BOOT"           # 1. write ldlinux.sys into the filesystem
dd bs=440 count=1 conv=notrunc if=mbr.bin of=$DEV   # 2. MBR boot code
fdisk … a … w                                  # 3. set the active/boot flag
mv "EFI/Boot" "$BOOT/../../EFI/"               # 4. hoist the EFI payload to the root
```

Step 4 is what makes the same stick UEFI-bootable — see [`uefi-usb-hdd.md`](uefi-usb-hdd.md).

Three behaviours worth knowing:

- **`ARCH` comes from `uname -m` of the machine running the script**, not from the Slax being
  installed. That is correct: the installer only has to write to a filesystem.
- **It works around `noexec` and `showexec`** by remounting, then `chmod`, then finally copying to
  `extlinux.exe`. Those paths exist because the usual target is a desktop-mounted FAT stick.
- **Step 4 is `mv`, not `cp`.** Running it twice from the same tree finds nothing to move, and the
  second run silently leaves the EFI payload where the first run put it — which is fine, but means
  re-running it is not idempotent in the way you might assume.

If the disk is already bootable, the script warns and waits for Enter first.

## The chain

```
BIOS
 └─ loads 440 B of MBR boot code from LBA 0        (mbr.bin)
     └─ finds the partition with the active flag
         └─ loads its volume boot record
             └─ ldlinux.sys      (written by extlinux --install)
                 ├─ ldlinux.c32, libcom32.c32, libutil.c32
                 ├─ reads /slax/boot/syslinux.cfg
                 └─ vesamenu.c32 → vmlinuz + initrfs.img
```

`ldlinux.sys` is **not on the ISO**. `extlinux --install` generates it, embedding the sector list of
its own blocks so the VBR can find it without parsing the filesystem. That is why you cannot install
a SYSLINUX bootloader by copying files, and why defragmenting or moving `ldlinux.sys` afterwards
breaks the boot.

## The menu — four entries, all live

`syslinux.cfg`, 1,537 bytes. Identical chrome to `isolinux.cfg`, different entries:

| label | adds to the cmdline |
|---|---|
| `default` — *Resume previous session* | `perchdir=resume automount` |
| `perch` — *Start a new session* | `automount perchdir=new` |
| `asksession` — *Choose session during startup* | `perchdir=ask` |
| `toram` — *Run Slax from RAM* | `toram` |

The whole difference from the CD menu is `perchdir=`. Persistence works here because the medium is
writable — see [`10-anatomy/union-and-persistence.md`](../10-anatomy/union-and-persistence.md).

Note `asksession` and `toram` both omit `automount`, so those two entries do not populate `/media/*`.

## Filesystem choice matters more than anything else

It decides which persistence strategy you get, and the difference is large:

| filesystem | persistence | size limit | |
|---|---|---|---|
| ext4, xfs, btrfs | `mount --bind` of the session directory | **none** | fastest, and what to pick if the stick is only ever used for Slax |
| FAT32 | DynFileFS container + XFS inside | 16 GB default, `perchsize=` to raise | also readable by Windows |
| NTFS | DynFileFS container | same | explicitly excluded from the bind path even though ntfs3 passes the POSIX test |

The choice is made at boot by a live test — create a file, symlink it, toggle the executable bit —
not by reading the filesystem type. A FAT32 stick therefore always ends up in a container, and an
ext4 stick always gets the bind mount.

## `dd` does not work

Writing a stock ISO to a stick with `dd` produces a stick that boots nothing. The system area of
every official image is **entirely zero**: no MBR boot code, no `0x55AA` signature, no partition
table.

```sh
head -c 32768 slax-64bit-debian-12.2.0.iso | tr -d '\0' | wc -c   # → 0
```

The `isohybrid` recipe fixes this by adding `isohdpfx.bin` as MBR boot code plus a GPT, after which
`dd` produces a stick that boots on BIOS and — combined with `uefi-bootable` — on UEFI too. See
[`docs/50-cookbook/isohybrid.md`](../50-cookbook/isohybrid.md).

A `dd`'d hybrid image still has one real limitation: the filesystem on it is ISO9660, so it is
**read-only, and therefore has no persistence**. If you want persistence, use `bootinst`.

## Hard disk installation

The same procedure against an internal partition. Two extra considerations:

- **`bootinst.sh` writes the MBR of the whole disk** and sets the active flag, which means it takes
  over the boot of that disk. If you already have a bootloader, do not run it — instead chainload
  from your existing GRUB:

  ```
  menuentry "Slax" {
      search --file --set=root /slax/boot/vmlinuz
      linux  /slax/boot/vmlinuz vga=normal rw printk.time=0 consoleblank=0 perchdir=resume automount
      initrd /slax/boot/initrfs.img
  }
  ```

  No `bootinst`, no MBR change, nothing overwritten. `find_data` locates `/slax/` on its own.
- **`from=` is worth setting explicitly** on a machine with several disks, so `find_data` does not
  spend its 45 seconds probing.

Longer form in [`docs/05-using-slax/install-to-harddisk.md`](../05-using-slax/install-to-harddisk.md).
