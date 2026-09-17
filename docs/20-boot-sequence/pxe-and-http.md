# Network booting — PXE and `from=http://`

Two unrelated mechanisms that both end with Slax running without a local medium. PXE loads the kernel
over the network; `from=http://` loads a normal kernel locally and then streams the ISO.

Neither is exercised by the shipped boot menus — both require hand-editing the command line or a
separate server — and both have known problems on Slackware.

## `from=http://…iso` — streaming an ISO

The simplest of the two, and it does not need a PXE server at all. Boot from anything that gets you
`vmlinuz` and `initrfs.img` — a small local stick, a netboot entry, a hypervisor — and give:

```
from=http://example.com/path/slax-64bit-debian-12.2.0.iso
```

`find_data` spots the `http://` prefix before doing anything else and calls `mount_data_http`:

```sh
init_network_ip                       # DHCP, or a static ip=
@mount.httpfs2 -r 9999 -t 5 $CACHE -c /dev/null "$1" "$2" -o ro
mount -o loop "$2"/* "$2"             # self-mount: the ISO over its own mountpoint
echo "$2/$LIVEKITNAME"
```

`@mount.httpfs2` is one of the seven static helpers beside busybox in the initramfs. It presents the remote file as
a local one via FUSE with HTTP range requests, and the ISO inside it is then loop-mounted **over the
same path**. Bundles are read on demand, so only the parts you actually touch cross the network.

### `cache=`

```sh
CACHE=$(cmdline_value cache | sed -r "s/[^0-9]//g" | sed -r "s/^0+//g")
[ "$CACHE" ] && CACHE="-C /tmp/httpfs.cache -S $(($CACHE*1024*1024))"
```

`cache=256` gives httpfs2 a 256 MB local cache in `/tmp`. Without it every re-read is another HTTP
request. On a slow link this is the difference between usable and not — and since `/tmp` is tmpfs,
the cache costs RAM.

### Network setup

`init_network_ip` runs either a static configuration from `ip=` or `udhcpc`:

```sh
ip=client:server:gateway:netmask        # static; gateway and server both become nameservers
                                        # omit entirely for DHCP
```

`init_network_dev` tries 26 named drivers in order — `3c59x`, `e1000`, `e1000e`, `tg3`, `r8169`,
`sky2` and so on — keeping the first that produces an interface, and falls back to
`modprobe_everything -e /drivers/net/` if none works. Anything newer than that hardcoded list still
works through the fallback, just more slowly.

**Wireless is not an option.** The initramfs carries 163 network modules, all wired
(`drivers/net/ethernet` and `drivers/net/phy`) — see
[`10-anatomy/initramfs.md`](../10-anatomy/initramfs.md).

## PXE

Triggered by the presence of `ip=` on the command line:

```sh
if [ "$(cmdline_value ip)" != "" ]; then download_data_pxe "$2"; return; fi
```

That test comes **after** the `http://` check, so `from=http://` wins if both are given.

### The chain

```
PXE firmware → DHCP → TFTP → pxelinux.0
                                 └─ pxelinux.cfg/… → vmlinuz + initrfs.img
                                                       └─ /init sees ip=, calls download_data_pxe
```

`pxelinux.0` (PXELINUX 6.03, 46,909 B) ships on the ISO but is never used by it. It belongs on the
TFTP server.

### Fetching the bundles

Unlike `from=http://`, PXE does not stream — it **downloads everything first**:

```sh
echo "$IP" | while IFS=":" read CLIENT SERVER GW MASK PORT; do
   [ "$PORT" = "" ] && PORT="7529"
   wget -q -O "$1/PXEFILELIST" "http://$SERVER:$PORT/PXEFILELIST?$(uname -r):$(uname -m)"
   …
done
```

| | |
|---|---|
| `ip=` form | `client:server:gateway:netmask[:port]` — note the **fifth** field |
| default port | **7529** |
| manifest | `PXEFILELIST`, requested with `?<kernel-version>:<machine>` so the server can vary the list per client |
| transport | HTTP first; **TFTP automatically on any failure** |
| TFTP parallelism | 3 concurrent jobs, split with `awk "NR % 3 == i-1"` |
| TFTP block size | `tftp -b 1486` |

The whole bundle set lands in RAM before boot continues, so PXE needs as much memory as `toram`.

### The server side

`/usr/bin/pxe` inside a running Slax sets up the server end. On Debian it works; **on Slackware it is
broken as shipped**:

- it calls `dhclient`, but Slackware uses `dhcpcd`;
- it calls `busybox httpd`, and there is no busybox in the Slackware rootfs — busybox exists only in
  the initramfs.

See [`30-inventory/known-upstream-bugs.md`](../30-inventory/known-upstream-bugs.md). The
`netboot-export` recipe is the offline answer: emit a PXE tree plus a `PXEFILELIST` from an ISO, to
be served by whatever web server you already have.

## `from=` more generally

`from=` is one parameter with five distinct behaviours, dispatched in this order:

| form | |
|---|---|
| `from=http://…` | httpfs2 stream — checked first |
| *(`ip=` present, no `from=`)* | PXE download |
| `from=ask` | `ncurses-menu` over live `blkid` output, refreshed once a second, showing each device's size and label |
| `from=/dev/sdb1/slax` | explicit device **and** path — `find_data_try` splits on the first three `/`-separated components |
| `from=/path/to/file.iso` | if the path resolves to a *file*, it is loop-mounted at `memory/iso` and the search continues inside it |
| `from=mydir` | a directory name to look for instead of `slax` |
| *(absent)* | defaults to `$LIVEKITNAME`, i.e. `slax` |

The ISO-file case is the one people miss: **you can keep a Slax ISO on an existing partition and boot
it without extracting anything.**

```
from=/dev/sda2/isos/slax-64bit-debian-12.2.0.iso
```

`find_data_try` mounts `/dev/sda2`, notices that the remainder names a file, loop-mounts it at
`memory/iso`, and searches inside. Combined with a GRUB entry on the host system, this is the least
invasive way to keep Slax on a machine that already has an OS.

## Testing without hardware

Both paths are testable under QEMU. The direct-kernel form skips the bootloader entirely and
exercises everything from `/init` onward, which is what `tests/boot/qemu_boot.py` uses:

```sh
qemu-system-x86_64 -m 2048 -nographic \
  -kernel work/iso/slax/boot/vmlinuz -initrd work/iso/slax/boot/initrfs.img \
  -cdrom out.iso -append "console=ttyS0 from=/dev/sr0/slax"
```

For the HTTP path, serve the ISO from the host and boot with `-netdev user` plus
`from=http://10.0.2.2:8000/slax.iso`; QEMU's user-mode networking maps `10.0.2.2` to the host.
