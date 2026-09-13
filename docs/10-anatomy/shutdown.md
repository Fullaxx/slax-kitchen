# Shutdown

Slax cannot simply halt. Its root filesystem is an aufs union over loop-mounted squashfs images,
some of which sit on a device that has to be unmounted cleanly — and the tools to take that apart
live *in* the root filesystem being taken apart. The solution is to pivot back into the initramfs,
which was kept alive at `/run/initramfs` for exactly this.

## The handoff

The initramfs is preserved because `change_root` used `pivot_root` rather than `switch_root`:

```sh
mount -t tmpfs tmpfs run
mkdir -p run/initramfs
pivot_root . run/initramfs
```

Both flavours then find `/run/initramfs/shutdown` and hand control to it — but by different routes.

### Debian — systemd does it

systemd checks for an executable `/run/initramfs/shutdown` at the end of shutdown and, if it finds
one, pivots into it and executes it with the shutdown verb as `$1`. No configuration, no unit file.
The comment at the top of the shipped script says exactly this:

```sh
# Shutdown script for initramfs. It's automatically started by
# systemd (if you use it) on shutdown, no need for any tweaks.
```

### Slackware — `rc.6` does it by hand

Slackware has no systemd, so `/etc/rc.d/rc.6` carries the pivot explicitly:

```sh
if [ -x /run/initramfs/shutdown ]; then
   cd /run/initramfs
   mkdir oldroot
   pivot_root . oldroot
   cd /
   mount -t proc proc proc
   mount -t sysfs sysfs sys
   # create bogus /sbin/init
   echo "#!/bin/sh"    >  /sbin/init
   echo "sleep 99999"  >> /sbin/init
   # re-execute /sbin/init (it will start the one in current pivot root)
   bin/chroot oldroot /sbin/telinit u
   exec bin/chroot . bin/sh shutdown $shutdown_command < dev/console > dev/console 2>&1
fi
```

The `bogus /sbin/init` is the interesting part. `telinit u` makes PID 1 re-exec itself; after the
pivot that resolves to the initramfs's `/sbin/init`, so a stub that sleeps forever is written first.
The effect is to detach PID 1 from the old root so it no longer holds it open.

Everything after `exec` runs from the initramfs — which is why it is reachable at all, since
`/oldroot` is about to be dismantled.

## What `/shutdown` does

2,504 bytes, and every step is about releasing a reference to something:

```sh
. /lib/config
. /lib/livekitlib
debug_start
debug_shell                     # `debug` gives you a shell here too
```

**1 · Detach unused loops**

```sh
mdev -s
losetup -a | cut -d : -f 1 | xargs -r -n 1 losetup -d
```

Re-populate `/dev` so every active loop device is visible, then detach the ones nothing holds.

**2 · Unmount the union**

```sh
umount_all /oldroot      # tac /proc/mounts, umount each, detach loops after each
```

Reverse mount order matters: `tac` unmounts children before parents.

**3 · Move whatever is still busy**

```sh
NR=100
tac /proc/mounts | cut -d " " -f 2 | grep ^/oldroot/. | while read LINE; do
   NR=$(($NR+1))
   mkdir -p /move/$NR
   mount --move $LINE /move/$NR 2>/dev/null
   umount /oldroot 2>/dev/null
done
```

Anything still mounted inside `/oldroot` is relocated to `/move/<N>`, which lets `/oldroot` itself go
even while its children are stuck.

**4 · Remember the boot device before losing it**

```sh
DEVICE="$(cat /proc/mounts | grep /memory/data | grep /dev/ | cut -d " " -f 1)"
```

This has to happen before the unmounts finish, because afterwards there is no record of where Slax
came from.

**5 · Four passes**

```sh
for i in 1 2 3 4; do
  for d in $(ls -1 /move 2>/dev/null | sort); do umount_all /move/$d; done
done
umount_all /memory
```

Repeated because releasing one mount can free the next, and there is no dependency graph to consult.

**6 · Eject the CD**

```sh
for i in $(cat /proc/sys/dev/cdrom/info | grep name); do
   if [ "$DEVICE" = "/dev/$i" ]; then
      eject /dev/$i
      echo "[  OK  ] CD/DVD tray will close in 6 seconds..."
      sleep 6
      eject -t /dev/$i
   fi
done
```

Only if `$DEVICE` from step 4 is an optical drive. It opens the tray, waits 6 seconds for you to take
the disc, then closes it. This uses the **standalone `eject` binary**, not busybox's applet — one of
the two shadowed names described in [`initramfs.md`](initramfs.md).

**7 · Halt**

```sh
if [ "$shutdown_command" = "reboot" ]; then reboot; reboot -f
else poweroff; poweroff -f; fi
```

The unforced call first, then `-f`. If both return, the script falls through to `reboot -f` and
finally to an interactive shell with the most honest error message in the codebase.

## Why it matters for customization

- **Do not remove `/run/initramfs`.** Anything that unmounts or clears it leaves no clean shutdown
  path: filesystems stay dirty, and a perch session on a journalled filesystem can need repair.
- **A replacement busybox is exercised hardest here.** `losetup -a | cut -d : -f 1` depends on
  busybox's `losetup` output format, `mdev -s` on its device-node rules, and `tac`, `xargs -r` and
  `mount --move` all have to behave. This path also runs with no filesystem left to log to, so a
  regression shows up as a hang rather than a message.
- **The Slackware path is the fragile one.** It is hand-written in `rc.6`; a customization that
  replaces or reorders `rc.6` can drop the `if [ -x /run/initramfs/shutdown ]` block and lose clean
  shutdown with no visible symptom until a filesystem check fails on the next boot.
- **`debug` works here too.** `debug_start` and `debug_shell` are called at the top, so the same boot
  parameter gives you a shell during shutdown.
