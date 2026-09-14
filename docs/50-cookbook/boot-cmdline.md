# `boot-cmdline` — bake in boot parameters

**Status: matrix-verified** — applied on all four targets; `APPEND` lines checked before and after against
the stock ISO.

```sh
kitchen apply boot-cmdline
```

Boot parameters are normally typed at the menu — press `Esc` in the four-second window, then `Tab`
to edit. That is fine once and useless at scale. An image handed to someone else, booted headless,
or written to twenty sticks needs its defaults already in the file.

## What it does

```
stock   APPEND vga=normal initrd=/slax/boot/initrfs.img … consoleblank=0 automount
after   APPEND vga=normal initrd=/slax/boot/initrfs.img … consoleblank=0 toram
```

It edits the `APPEND` line of **existing** entries rather than adding a new one, so the parameters
apply however the machine boots — including whichever entry the user actually picks.
[`boot.menu`](../90-reference/verbs.md#bootmenu---bootcmdline-) is the verb for adding a whole new
entry; [`serial-console`](serial-console.md) is an example of that.

## `append` adds, `remove` drops by key

```yaml
- verb: boot.cmdline
  append: [toram]
- verb: boot.cmdline
  remove: [automount]         # drops both `automount` and `automount=x`
  labels: [slax]              # optional: restrict to these LABELs
```

Removal is **by key**, so `automount` matches a bare flag and a `key=value` form alike. Appending a
key that is already present replaces it rather than duplicating it.

Both `isolinux.cfg` and `syslinux.cfg` are edited by default. Upstream keeps the two deliberately
different — the CD's `isolinux.cfg` has no persistence entries, `syslinux.cfg` (USB/HDD) does — so
**the entry counts differ per file, and that is expected**:

```
isolinux.cfg: cmdline updated on 2 entries
syslinux.cfg: cmdline updated on 3 entries      <- toram
isolinux.cfg: cmdline updated on 2 entries
syslinux.cfg: cmdline updated on 2 entries      <- automount; one entry never had it
```

The count is what actually **changed**. Re-applying reports `0 entries`, which is how you can tell
a step did nothing.

## Why `toram`

It copies the whole image to RAM and frees the medium, so the stick can be pulled and the desktop
stops hitting slow USB. It costs RAM equal to the image size, which is exactly why upstream does
not default it — make that trade deliberately.

## Why `automount` is *removed* rather than negated

This is the documented workaround for
[issue 12](../30-inventory/known-upstream-bugs.md). `fstab_create` in the initramfs tests:

```sh
grep -vq automount /proc/cmdline
```

The string `noautomount` **contains** `automount`, so the test never short-circuits and automount
proceeds anyway. Negating the flag cannot work — the flag has to not be there. The stock entries
include it, which is why this takes a recipe rather than a boot-time edit.

The same substring trap applies to `debug`, `perch` and `toram`; only `text` is word-anchored.

## Parameters worth knowing

| | |
|---|---|
| `toram` | copy to RAM, release the medium |
| `text` | skip the desktop entirely (word-anchored, so `notext` does nothing) |
| `noload=05-chromium` | skip a bundle this boot |
| `from=/dev/sdb1/slax` | look for the Slax data somewhere specific |
| `perchdir=resume\|new\|ask` | persistent changes (USB/HDD only) |
| `debug` | drop to a shell at six points during init |

Full list in [boot parameters](../20-boot-sequence/boot-parameters.md).
