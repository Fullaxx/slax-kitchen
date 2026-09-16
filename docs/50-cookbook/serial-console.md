# `serial-console` — capture the whole boot on a serial port

**Status: boot-verified** — entry present in both menus on all four targets, and booted through it on `debian-64bit-12.2.0` via isolinux, GRUB/OVMF and usb-storage, reaching all three livekit markers each time.

```sh
kitchen apply serial-console
```

Adds one boot entry. Nothing else on the ISO changes, and the stock entries are untouched, so the
image still boots normally for anyone who does not pick it.

## Why this one comes first

**It is the prerequisite for every automated boot test.** QEMU with `-nographic` captures `ttyS0`
to a file, so the livekit init messages become machine-readable text instead of pixels:

```
Looking for slax data
Mounting bundles
Setting up empty union using aufs
Live Kit done, starting slax
```

Without a serial entry you are reading a framebuffer screenshot and guessing. With one, a test can
assert on exact strings and tell you *which stage* a failed boot reached — which is the difference
between "it did not boot" and "`find_data` timed out after 45 seconds".

The stock menus have no serial entry, which is why this recipe exists before any of the testing
tiers do.

## What it adds

```
LABEL serial
  MENU LABEL Slax (serial console on ttyS0)
  KERNEL /slax/boot/vmlinuz
  APPEND vga=normal initrd=/slax/boot/initrfs.img load_ramdisk=1 prompt_ramdisk=0 rw
         printk.time=0 consoleblank=0 automount console=tty0 console=ttyS0,115200n8
```

Into **both** `isolinux.cfg` (CD) and `syslinux.cfg` (USB/HDD/UEFI). Since
`EFI/Boot/syslinux.cfg` is one line — `INCLUDE /slax/boot/syslinux.cfg` — the UEFI menu picks it up
for free.

### `console=` twice, and the order is the whole recipe

```
console=tty0 console=ttyS0,115200n8
```

Linux sends kernel messages to **every** `console=` given, but `/dev/console` — where userspace
lands — is the **last** one.

**This used to be the other way round, and it made the recipe useless for its stated purpose.**
With `console=ttyS0 console=tty0`, `/dev/console` was the screen, so only the *kernel* wrote to
the serial port. Everything livekit prints — it writes to stderr — went to video. Measured on
2026-09-16, booting this entry through isolinux and capturing ttyS0:

| | through the serial menu entry | direct kernel boot |
|---|---|---|
| bytes of serial log | 21,174 | 21,458 |
| `Looking for slax data` | **0** | 1 |
| `Mounting bundles` | **0** | 1 |
| `Live Kit done` | **0** | 1 |
| `### TESTKIT BEGIN` | **0** | 1 |

Twenty-one kilobytes of kernel log and not one line from the thing being tested. The page said
"logs the whole boot to the serial port"; it logged the kernel's half.

Serial now comes last, so `/dev/console` is the serial port and a capture sees what init prints.
The screen still shows kernel messages — what moved is login and userspace output. If you want the
old arrangement, [`boot-cmdline`](boot-cmdline.md) rewrites an `APPEND` line in place.

This is why [`kitchen test --bios`](../60-testing/ci.md) can assert anything at all: it points the
bootloader at this entry, and before the reversal there was nothing on the wire to assert.

`115200n8` is 115200 baud, no parity, 8 data bits. Match whatever is reading the other end.

## Variables

```yaml
vars:
  port: ttyS0
  speed: "115200"
```

Override per build in a profile — the recipe file stays untouched:

```yaml
recipes:
  - serial-console                              # the recipe's own defaults
  - name: serial-console
    vars: {port: ttyS1, speed: "9600"}          # or override them here
```

Verified: that profile produces `console=ttyS1,9600n8` on the `serial` menu entry, and
`kitchen status` records which values were used. See
[recipes in a fork](../40-workflow/recipes-in-a-fork.md).

## Using it

**QEMU** — what the test harness does:

```sh
qemu-system-x86_64 -m 2048 -cdrom out/slax-custom.iso -nographic
```

`-nographic` wires `ttyS0` to your terminal. Pick the *Slax (serial console…)* entry — press `Esc`
at the menu first, since it is hidden behind the splash.

Driving that menu without a keyboard is what `tests/boot/qemu_boot.py --keys` is for:

```sh
kitchen test out/slax-custom.iso --bios --seconds 120
```

**Real hardware** — a null-modem cable or a USB-serial adapter, then:

```sh
screen /dev/ttyUSB0 115200        # or: minicom -D /dev/ttyUSB0 -b 115200
```

**A VM with a log file** rather than an attached terminal:

```sh
qemu-system-x86_64 -m 2048 -cdrom out.iso -display none -serial file:/tmp/boot.log
```

## Combining it

| with | why |
|---|---|
| `uefi-bootable` | the GRUB menu is generated from `isolinux.cfg`, so the serial entry is mirrored automatically — but **apply `uefi-bootable` last** or it will not see this entry |
| `boot.cmdline` | to make serial the *default* rather than an extra entry, on a headless build |
| `rootcopy-overlay` | drop a `preinit.sh` that writes markers to `/dev/console`, and they land in the same log |

## Making it the default instead

Adding an entry is the safe shape — it leaves a way back. If you genuinely want every boot on
serial, edit the existing entries instead:

```yaml
- verb: boot.cmdline
  targets: [isolinux.cfg, syslinux.cfg]
  append: ["console=ttyS0,115200n8"]
```

Note this appends only; the stock entries have no `console=` at all, so the result is a single
`console=` and the screen goes quiet. On a machine with no serial port attached that is a boot with
no visible output whatsoever — reversible only by editing the ISO again.

## Limits

- **The bootloader menu itself is not on the serial port.** SYSLINUX needs `SERIAL 0 115200` as a
  global directive for that, which this recipe does not add — it would change the menu for every
  entry, including the stock ones. You see the menu on screen and the kernel onward on serial.
- **`printk.time=0` is inherited** from the stock command line, so messages carry no timestamps.
  Add `printk.time=1` to the `append` if you are timing boot stages.
