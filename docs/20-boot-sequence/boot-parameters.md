# Boot parameters

The authoritative list, built by grepping every consumer in `livekitlib`, `/init`, `/shutdown` and
the helper scripts inside `01-core.sb` — not from `help.txt`, which documents six of them and one
that does not exist.

Press `Esc` at the boot menu, then `Tab`, to edit the command line for one boot. To bake a parameter
in permanently, use the `boot.cmdline` verb.

## The stock command line

```
vga=normal initrd=/slax/boot/initrfs.img load_ramdisk=1 prompt_ramdisk=0
rw printk.time=0 consoleblank=0 automount
```

plus, from the USB/HDD menu only, one of `perchdir=resume`, `perchdir=new`, `perchdir=ask`, or
`toram`.

## Every parameter Slax reads

| parameter | read by | |
|---|---|---|
| `from=` | `find_data` | directory · `/dev/X/path` · `file.iso` · `http://…` · `ask`. See [`pxe-and-http.md`](pxe-and-http.md) |
| `toram` | `copy_to_ram` | copy everything to RAM, then unmount the medium |
| `perch` | `persistent_changes` | enable persistence. **Substring match** — `perchdir=` and `perchsize=` each imply it |
| `perchdir=` | `restore_perch_session` | `resume` · `new` · `ask` · `<N>` · `/dev/sdXN/path` |
| `perchsize=` | `persistent_changes` | container size in MB. Accepts `G`/`GB`/`T`/`TB`. Minimum **and** default 16000 |
| `noload=` | `filter_noload` | regex of bundles to skip. **Commas become `\|`** |
| `load=` | `filter_load` | regex of bundles to load. **Commas are not translated**, and it applies only to `modules/` |
| `debug` | `debug_start` | shell at six points in `/init`, and again in `/shutdown` |
| `automount` | `fstab_create`, `slax-automount` | add `/media/<dev>` entries for every other disk |
| `noautomount` | `slax-automount` **only** | see the note below — it is half-implemented |
| `ip=` | `init_network_ip`, `download_data_pxe` | `client:server:gw:mask[:port]`. Presence alone triggers PXE |
| `cache=` | `mount_data_http` | httpfs2 cache size in MB |
| `text` | `xorg.service` / `Slax.03.startup` | skip X, console only |

Anything else on the command line is passed to the kernel and ignored by Slax.

## Parsing, and what it implies

```sh
cmdline_value() {
   cat /proc/cmdline | egrep -o "(^|[[:space:]])$1=[^[:space:]]+" | tr -d " " | cut -d "=" -f 2- | tail -n 1
}
```

- **Values cannot contain spaces.** There is no quoting; the match ends at the first whitespace.
- **The last occurrence wins** (`tail -n 1`), so appending a second `from=` overrides an earlier one
  rather than conflicting with it. Useful at the `Tab` prompt.
- **A key must be preceded by start-of-line or whitespace.** This is what stops `noload=` from being
  seen as a `load=`.

The bare flags are different, and less careful:

```sh
grep -q  debug     /proc/cmdline     # debug
grep -vq perch     /proc/cmdline     # perch
grep -vq toram     /proc/cmdline     # toram
grep -vq automount /proc/cmdline     # automount
grep -q -w text    /proc/cmdline     # text  ← the only word-anchored one
```

They are plain substring tests. `perchdir=new` contains `perch`, which is intended. But it also
means a parameter that merely *contains* one of these words switches it on — `nodebug` enables
`debug`, and `notoram` enables `toram`.

### `noautomount` is half-implemented

Two components disagree:

| | reads | result with `noautomount` |
|---|---|---|
| `fstab_create` (initramfs) | `grep -vq automount` | **automount proceeds** — "noautomount" contains "automount" |
| `slax-automount` (udev) | `grep -q noautomount` first | correctly exits |

Demonstrated:

```sh
$ echo 'vga=normal rw noautomount' > /tmp/c
$ grep -vq automount /tmp/c && echo skip || echo "proceed  <-- noautomount ignored"
proceed  <-- noautomount ignored
```

So booting with `noautomount` still writes `/media/*` entries into `/etc/fstab` at boot, but no new
hotplug mounts appear afterwards. To actually get neither, omit `automount` instead of negating it.

Filed in [`30-inventory/known-upstream-bugs.md`](../30-inventory/known-upstream-bugs.md).

## Documented but not implemented

**`nosound`** appears in `help.txt`:

> `* nosound: If specified, sound devices are muted during startup`

There is no consumer. Not in the initramfs, not in `livekitlib`, and not anywhere in `01-core.sb`,
`02-xorg.sb` or `03-desktop.sb` on either flavour:

```sh
grep -rlI nosound <unpacked bundles> <initramfs>    # → nothing
```

It is vestigial. Setting it does nothing at all.

## Implemented but not documented

**`load=`** is the counterpart of `noload=` and appears in no documentation upstream. It has two
asymmetries with `noload=` that are easy to trip over:

```sh
filter_load()   { FILTER=$(cmdline_value load);   … egrep    "$FILTER"; }
filter_noload() { FILTER=$(cmdline_value noload); FILTER=${FILTER//,/|}; … egrep -v "$FILTER"; }
```

1. **`noload=` translates commas to `|`; `load=` does not.** `noload=xorg,chromium` works;
   `load=xorg,chromium` matches the literal string `xorg,chromium` and therefore loads nothing. Write
   `load=xorg|chromium`.
2. **`load=` applies only to bundles under `modules/`.** `mount_bundles` concatenates the top-level
   listing and the `modules/` listing, and pipes only the second through `filter_load`. `noload=` is
   applied to both.

`cache=`, `ip=` and `text` are also undocumented in `help.txt`, though they are at least mentioned in
upstream's `DOC/boot_parameters.txt` — which itself covers only two parameters. See
[`15-upstream/doc-verbatim.md`](../15-upstream/doc-verbatim.md).

## Worked examples

```sh
# smaller boot: drop the browser and the dev tools
noload=05-chromium,06-devel

# load only the core and X
load=01-core|02-xorg

# run from RAM, eject the stick, keep nothing
toram

# persistent session on a second stick while booted from CD
perchdir=/dev/sdb1/slax/changes

# 32 GB container on a FAT stick, set once at creation
perchdir=new perchsize=32GB

# boot an ISO stored on an existing partition
from=/dev/sda2/isos/slax-64bit-debian-12.2.0.iso

# headless, for CI — the serial-console recipe bakes this into a menu entry
text console=ttyS0,115200n8

# find out where it is failing
debug
```

## Baking them in

```yaml
- verb: boot.cmdline
  targets: [isolinux.cfg, syslinux.cfg]
  append: ["noload=05-chromium"]
```

`boot.cmdline` edits the `APPEND` lines of existing entries; `boot.menu` adds new entries alongside
them. Adding an entry is usually better than changing the default, because it leaves a way back if
the change turns out to be wrong. Reference:
[`docs/90-reference/cli.md`](../90-reference/cli.md).
