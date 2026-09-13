# Boot modes and parameters

Press **Esc** at the boot menu to reveal it, then **Tab** to edit the command line.

## Every parameter that actually does something

Taken from the code rather than the help screen, which is incomplete in both directions.

| Parameter | Effect |
|---|---|
| `toram` | copy all Slax data to RAM, then unmount the medium so you can remove it |
| `text` | skip the desktop entirely; console only |
| `perch`, `perchdir=`, `perchsize=` | persistence — see [persistence-perch](persistence-perch.md) |
| `noload=REGEX` | skip bundles. Commas are translated to `\|`, so `noload=04-apps,05-chromium` works |
| `load=REGEX` | the inverse whitelist. **Works but is undocumented upstream**, and unlike `noload` it does *not* accept commas |
| `from=…` | where to look for Slax data (see below) |
| `automount` | mount every other disk under `/media/` and generate `/etc/fstab` |
| `noautomount` | override the above |
| `debug` | drop to a shell at six points during init — the best tool for a boot that fails |
| `ip=client:server:gw:mask[:port]` | PXE boot |
| `cache=MB` | cache size when booting over HTTP |

⚠️ **`nosound` is listed on the F1 help screen but does nothing.** It has no implementation anywhere
in the current codebase — a leftover from Slax 9.x.

## `from=` — finding the data

By default the initramfs scans every block device for `/slax/` containing `.sb` files, retrying for
45 seconds. `from=` overrides that:

| Form | Example |
|---|---|
| a directory | `from=/slax` |
| a device plus path | `from=/dev/sdb1/slax` |
| **an ISO file on disk** | `from=/dev/sda2/isos/slax.iso` — loop-mounted automatically |
| **an ISO over HTTP** | `from=http://server/slax.iso` |
| a menu | `from=ask` — lists devices with size and label |

Booting an ISO file straight off a hard disk is genuinely useful: no partitioning, no USB stick, just
a file and a bootloader entry.

## Booting over HTTP

```
from=http://example.org/slax.iso cache=256
```

The initramfs mounts the remote ISO with `httpfs2` and reads bundles on demand over HTTP. `cache=`
sets a local cache in MB. Slow to start, but it works, and `toram` makes it usable afterwards.

## PXE

Slax can serve itself. On a running machine:

```sh
pxe
```

That rebuilds an initramfs containing every wired network driver, symlinks the kernel, `pxelinux.0`
and all bundles into a TFTP root, writes a `PXEFILELIST`, then starts `dnsmasq` for DHCP+TFTP and a
BusyBox HTTP server on **port 7529** — which is what "slax" spells on a phone keypad.

Clients boot with `ip=client:server:gw:mask`, fetch the file list over HTTP (falling back to TFTP)
and pull the bundles.

## `toram`

Copies everything into memory, then unmounts — and if you booted from an ISO file, unmounts the
filesystem holding it too. Needs RAM for the whole image (about 420 MB plus working space), makes
everything faster, and lets you take the stick out.

## Useful combinations

| | |
|---|---|
| `toram noload=05-chromium` | fast, in RAM, no browser |
| `text noload=02-xorg\|03-desktop` | minimal console system |
| `perchdir=ask` | pick a session at boot |
| `debug` | stop at six checkpoints to see where boot fails |
| `from=/dev/sda3/slax perchdir=/dev/sda3/slax/changes` | boot from one partition, save to it |

## With slax-kitchen

`boot.cmdline` bakes any of these into the shipped menu, and `boot.menu` adds entries. The
[`serial-console`](../50-cookbook/) recipe adds an entry with `console=ttyS0,115200n8` for headless
machines — the stock menu has nothing equivalent.
