# BIOS + optical media

The only route a stock Slax ISO supports, and the one the image is actually built for.

## The chain

```
BIOS
 └─ reads the El Torito catalog at LBA 45
     └─ loads 4 × 512 B from LBA 46, no emulation, to 0x07C0
         └─ isolinux.bin  (ISOLINUX 6.03, 40,960 B)
             ├─ reads its own boot-info-table to learn where it lives
             ├─ loads ldlinux.c32, libcom32.c32, libutil.c32
             ├─ reads /slax/boot/isolinux.cfg
             └─ vesamenu.c32 draws the menu over bootlogo.png
                 └─ vmlinuz + initrfs.img, via BIOS INT 13h
```

### Step 1 — El Torito

The BIOS finds the Boot Record descriptor at LBA 17, follows it to the catalog at LBA 45, reads the
single entry there, and loads **4 sectors — 2 KiB — from LBA 46** in no-emulation mode. Full byte
dump in [`10-anatomy/el-torito.md`](../10-anatomy/el-torito.md).

2 KiB is less than `isolinux.bin`'s 40,960 bytes. The first 2 KiB is a loader stub that pulls in the
rest.

### Step 2 — `isolinux.bin` locates itself

It cannot use a path, because it does not yet know how to read the filesystem it is on. Instead it
reads the **boot-info-table** that `genisoimage -boot-info-table` patched into its own bytes 8–63:

```
pvd_lba  = 16      file_lba = 46
file_len = 40960   checksum = 0xe5d3e1ef
```

`file_lba` tells it where it is; `pvd_lba` tells it where the volume descriptor is. From there it can
parse ISO9660 and read files by name — starting with `ldlinux.c32` from its own directory.

This is why **`isolinux.bin` must never be patched after mastering**: any change past offset 64
invalidates the checksum and the loader refuses to run.

### Step 3 — the menu

`isolinux.cfg`, 1,202 bytes, in the same directory:

```
UI /slax/boot/vesamenu.c32
PROMPT 0
TIMEOUT 40
MENU HIDDEN
MENU HIDDENKEY Enter default
MENU BACKGROUND /slax/boot/bootlogo.png
F1 help.txt /slax/boot/zblack.png
```

`TIMEOUT 40` is **4 seconds** — SYSLINUX counts tenths. `MENU HIDDEN` means you see the splash and
the countdown, not the list; `Esc` reveals the entries, `Tab` edits the command line, `F1` shows
`help.txt`.

Two live entries and two dead ones:

| label | |
|---|---|
| `default` | *Run Slax from CD* |
| `toram` | *Run Slax from RAM (Copy to RAM)* — adds `toram` |
| `sessiondisabled` | *Restore previous session* — `MENU DISABLED` |
| `newsessiondisabled` | *Start a new session* — `MENU DISABLED` |

The two disabled entries exist so the CD menu is the same height as the USB one. They are not a bug
and cannot be enabled by editing them: persistence needs a writable medium.

### Step 4 — the kernel

```
APPEND vga=normal initrd=/slax/boot/initrfs.img load_ramdisk=1 prompt_ramdisk=0 \
       rw printk.time=0 consoleblank=0 automount
```

ISOLINUX reads both files through the BIOS and hands off. `/init` then mounts `/dev/sr0`, finds
`/slax/modules/*.sb`, and proceeds exactly as on any other medium — see
[`10-anatomy/livekit-init.md`](../10-anatomy/livekit-init.md).

## No persistence, ever

`isolinux.cfg` sets no `perchdir=`, and `persistent_changes` only runs when the substring `perch`
appears on the command line. Adding `perchdir=new` by hand at the `Tab` prompt does enable the code
path, but the CD is read-only, so the writability test fails and you get:

```
* Persistent changes not writable or not used
```

To persist from an optical boot you must point at a different device:

```
perchdir=/dev/sdb1/slax/changes
```

That works — `persistent_changes` accepts an explicit path — but at that point the stick is doing the
writing and you may as well boot from it.

## What eject does

On shutdown, `/shutdown` compares the boot device against `/proc/sys/dev/cdrom/info`, and if they
match it opens the tray, waits 6 seconds, and closes it again. See
[`10-anatomy/shutdown.md`](../10-anatomy/shutdown.md).

With `toram` the medium is already unmounted before the desktop appears, so the disc can be taken
out at any time.

## Customizing this path

| change | how |
|---|---|
| menu entries, defaults, timeout | edit `isolinux.cfg` — `boot.menu` / `boot.cmdline` |
| add a boot payload (memtest) | `boot.payload` + `boot.menu`. **Use `LINUX`, not `KERNEL`** |
| branding | replace `bootlogo.png`, `zblack.png`, `help.txt` |
| rename `slax/` | requires patching `isolinux.bin` — see [`10-anatomy/bootloader-payloads.md`](../10-anatomy/bootloader-payloads.md) |

The `KERNEL` vs `LINUX` distinction is not cosmetic. `KERNEL` makes ISOLINUX guess the payload type
from the file extension; for a raw `memtest.bin` it guesses wrong and you get a **silent black screen
with no error and no timeout**. `LINUX` forces the bzImage path, which is what Memtest86+ actually
is. This cost real debugging time; see
[`docs/50-cookbook/memtest86plus.md`](../50-cookbook/memtest86plus.md).
