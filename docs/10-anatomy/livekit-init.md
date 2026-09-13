# `/init`, start to finish

The initramfs `/init` is 2,317 bytes and does almost nothing itself: it sources `lib/config` and
`lib/livekitlib`, then calls eighteen functions in order. This page follows that order and records
**what state each step leaves behind**, because that state is what a customization has to work with.

The per-function reference — signatures, line numbers, gotchas — is
[`15-upstream/livekitlib-reference.md`](../15-upstream/livekitlib-reference.md). This page does not
repeat it.

```sh
xz -dc initrfs.img | cpio -i --to-stdout init
```

## The script

```sh
export PATH=.:/:/usr/sbin:/usr/bin:/sbin:/bin
. /lib/config
. /lib/livekitlib
transfer_initramfs
MEMORY=/memory; CHANGES=$MEMORY/changes; UNION=$MEMORY/union
DATAMNT=$MEMORY/data; BUNDLES=$MEMORY/bundles
header "Live Kit init <http://www.linux-live.org/>"
init_proc_sysfs
debug_start
init_devs; init_aufs; init_zram
modprobe_everything -v /drivers/net/
init_blkid_cache
DATA="$(find_data 45 "$DATAMNT")"
check_data_found "$DATA"
persistent_changes "$DATA" "$CHANGES"
DATA="$(copy_to_ram "$DATA" "$CHANGES")"
mount_bundles "$DATA" "$BUNDLES"
init_union "$CHANGES" "$UNION" "$BUNDLES"
union_append_bundles "$BUNDLES" "$UNION"
copy_rootcopy_content "$DATA" "$UNION"
fstab_create "$UNION" "$DATAMNT"
user_preinit "$DATA" "$UNION"
header "Live Kit done, starting $LIVEKITNAME"
change_root "$UNION"
```

**`PATH` starts with `.` and `/`.** The current directory precedes every system directory. It is how
the script finds its own helpers before `/bin` is populated, and it is a hardening consideration for
anything that replaces busybox — `CONFIG_FEATURE_PREFER_APPLETS` closes the gap.

## Stage by stage

### 1 · `transfer_initramfs` — escape the initramfs

```sh
mount -t tmpfs -o size="100%" tmpfs /m
cp -a /??* /m
echo "…" > /m/lib/initramfs_escaped
exec switch_root -c /dev/console . $0
```

Copies everything to a **tmpfs** and re-executes itself there. The sentinel file
`/lib/initramfs_escaped` is what stops the second invocation looping.

This exists because you cannot `pivot_root` out of an initramfs (rootfs), only `switch_root` into it
— and `switch_root` destroys the old root, which would take `/shutdown` with it. Moving to a tmpfs
first means `change_root` can use `pivot_root` later and keep the initramfs alive at
`/run/initramfs`, which is the whole reason the shutdown path works. See
[`shutdown.md`](shutdown.md).

Note `cp -a /??*` — **two-or-more-letter names only.** A single-letter top-level directory in the
initramfs would be silently dropped.

*Leaves:* the initramfs running from tmpfs, `/` writable.

### 2 · `init_proc_sysfs`

Mounts `/proc` and `/sys`, sets `printk` to 0 so kernel messages stop overwriting the boot output,
remounts `/` rw, and symlinks `/etc/mtab → /proc/mounts`.

*Leaves:* `/proc`, `/sys`, a usable `mtab`. Kernel messages suppressed — which is why `debug` is the
only way to see what is happening when boot fails.

### 3 · `init_devs`, `init_aufs`, `init_zram`

```sh
modprobe zram loop squashfs fuse      # init_devs
modprobe aufs || modprobe overlay     # init_aufs
echo 536870912 > /sys/block/zram0/disksize; mkswap; swapon; swappiness=100
```

`init_aufs` is the fork in the road for everything downstream. **If `aufs.ko` does not load, Slax
runs on overlayfs** — and `union_append_bundles` becomes a no-op, `slax activate` stops working, and
`savechanges` behaves differently. Nothing warns you; the only visible sign is the boot message
`Setting up union using overlayfs` instead of `Setting up empty union using aufs`.

`init_zram` gives 512 MB of compressed swap with `swappiness=100`, so RAM-resident changes spill into
compressed RAM rather than failing. On a machine with no `zram0` it silently does nothing.

*Leaves:* loop, squashfs, fuse, a union filesystem, and swap.

### 4 · `modprobe_everything -v /drivers/net/`

```sh
find /lib/modules/ | fgrep .ko | egrep -v /drivers/net/ | sed -r "s:^.*/|[.]ko$::g" | xargs -n1 modprobe
```

Loads all 301 modules except the 163 network drivers — storage and USB are needed to find the data,
networking is not, and probing 163 NICs costs time. Network drivers are loaded later and only if
`from=http://` or PXE is in play.

Then `refresh_devs`: `mdev -s`, plus `echo /sbin/mdev > /proc/sys/kernel/hotplug` so nodes keep
appearing as devices settle.

*Leaves:* `/dev` populated for every storage and USB controller present.

### 5 · `init_blkid_cache`, then `find_data 45 "$DATAMNT"`

The heart of boot, and the one that takes the time. `find_data` retries for **45 seconds**, calling
`refresh_devs` every second, because USB mass storage can take a while to enumerate.

For each device `blkid` reports — skipping `/loop`, `/ram`, `/zram` — it mounts the device and looks
for `$FROM/*.sb` **or** `$FROM/modules/*.sb`. First hit wins. `from=` overrides the search and can
name a directory, a `/dev/X/path`, an **ISO file** (loop-mounted in turn), an `http://` URL
(httpfs2), or `ask` for a menu.

`check_data_found` calls `fatal` if nothing turned up — the `Cannot find Slax data` message.

*Leaves:* the medium mounted at `/memory/data`, and `$DATA` naming the directory containing the
bundles.

### 6 · `persistent_changes "$DATA" "$CHANGES"`

Runs only if the substring `perch` appears in `/proc/cmdline`. Full behaviour in
[`union-and-persistence.md`](union-and-persistence.md).

*Leaves:* `/memory/changes` either bind-mounted to a session directory, mounted from an XFS image
inside a DynFileFS container, or left as plain tmpfs if persistence was not requested or not possible.

### 7 · `copy_to_ram "$DATA" "$CHANGES"`

Runs only with `toram`. Copies `$DATA/*` to `/memory/toram`, then unmounts the medium — **and, if the
data came from an ISO file, unmounts the filesystem that ISO was on too.** That is what lets you
eject the stick.

*Leaves:* `$DATA` re-pointed at `/memory/toram`; no medium mounted.

### 8 · `mount_bundles "$DATA" "$BUNDLES"`

```sh
( ls -1 "$1" | sort -n ; cd "$1" ; find modules/ | sortmod | filter_load ) \
  | grep '[.]sb$' | filter_noload | while read BUNDLE; do
      mount -o loop,ro -t squashfs "$1/$BUNDLE" "$2/$(basename $BUNDLE)"
  done
```

Two sources concatenated: bundles sitting **directly in `/slax/`** first, then those in
`/slax/modules/`. So a `.sb` dropped in `/slax/` mounts *before* everything in `modules/` and
therefore ends up with **lower** priority — the opposite of what most people expect from "put it at
the top level".

An asymmetry worth knowing: **`filter_load` is applied only to the `modules/` branch**, while
`filter_noload` is applied to the concatenation of both. So `noload=` can exclude a top-level bundle
and `load=` cannot select one.

*Leaves:* every bundle loop-mounted read-only under `/memory/bundles/<name>.sb`.

### 9 · `init_union` then `union_append_bundles`

```sh
mount -t aufs -o xino="/.xino",trunc_xino,br="$CHANGES" aufs "$UNION"    # empty, one writable branch
find "$BUNDLES" -mindepth 1 -maxdepth 1 | sortmod | while read B; do
   mount -o remount,add:1:"$B=rr+wh" aufs "$UNION"
done
```

The union starts with only the writable changes branch, and each bundle is inserted **at index 1** —
directly below the writable branch, above everything inserted before it. Since bundles arrive in
ascending numeric order, **the highest number wins.** That single rule is the basis of almost every
customization here: a `07-mytools.sb` overrides `01-core.sb`, and `savechanges` writes
`99-changes-N.sb` so a saved session outranks everything.

Under overlayfs `init_union` builds the whole `lowerdir` chain up front with `sortmod | tac`,
reaching the same precedence — and `union_append_bundles` then has nothing to do.

*Leaves:* `/memory/union` — a complete root filesystem.

### 10 · `copy_rootcopy_content "$DATA" "$UNION"`

```sh
cp -a "$DATA"/rootcopy/* "$UNION"
```

A plain copy into the writable branch, before anything else runs. No squashfs rebuild, no
precedence to reason about — whatever is in `rootcopy/` wins over every bundle because it lands in
the writable layer. The directory does not exist on any shipped ISO, so creating it is purely
additive.

This is the cheapest customization mechanism Slax has, and it is what
[`rootcopy-overlay`](../50-cookbook/rootcopy-overlay.md) uses.

### 11 · `fstab_create "$UNION" "$DATAMNT"`

Writes five fixed lines — `aufs /`, `proc`, `sysfs`, `devpts`, `tmpfs /dev/shm` — then, **if
`automount` is on the cmdline**, appends one entry per `blkid` device that is not a loop, zram, swap,
squashfs, or the boot device itself, mounting each at `/media/<basename>`.

`/etc/fstab` exists in no bundle on any image. It is generated here, every boot.

Options are `defaults,noatime,nofail,x-systemd.device-timeout=10` — written unconditionally, so the
systemd-specific option appears on Slackware too, where it is simply ignored.

### 12 · `user_preinit "$DATA" "$UNION"`

```sh
. "$DATA/rootcopy/run/preinit.sh" "$UNION"
```

**Sourced, not executed.** It runs inside `/init`'s own shell, with `$1` set to the union path, every
`livekitlib` function in scope, and the union not yet pivoted. An `exit` here ends the boot.

That makes it the last point at which you can change anything before userspace starts — and the only
hook that sees both the initramfs and the assembled root at the same time.

### 13 · `change_root "$UNION"`

```sh
mkdir -p boot dev proc sys tmp media mnt run ; chmod 1777 tmp
mknod dev/{console,tty,tty0,tty1,null} …      # only if missing
ln -s /bin/true sbin/fsck.aufs                # only if missing
mount -t tmpfs tmpfs run ; mkdir -p run/initramfs
mount -n -o remount,ro aufs .
pivot_root . run/initramfs
exec $CHROOT . $INIT < dev/console > dev/console 2>&1
```

Four things happen here that are easy to miss and matter a lot:

1. **The runtime directories are created at boot, not shipped in a bundle.** `boot dev proc sys tmp
   media mnt run` and five device nodes are made here if absent. This is why Debian's `01-core.sb`
   can legitimately omit all of them — see [`bundles-squashfs.md`](bundles-squashfs.md).
2. **`fsck.aufs` is symlinked to `/bin/true`** so init does not try to check the root.
3. **The union is remounted read-only** just before the pivot. The kernel cmdline carries `rw`, and
   the real init remounts it writable — so a boot that stalls before that point leaves you with a
   read-only root, which is a useful diagnostic signal.
4. `chroot` and `init` are **searched for** across `bin`, `sbin`, `usr/bin`, `usr/sbin` — so a bundle
   that provides `init` anywhere sensible works, and one that provides it nowhere gets
   `Can't find executable init command`.

*Leaves:* the union as `/`, the initramfs preserved at `/run/initramfs`, and PID 1 replaced by
systemd (Debian) or sysvinit (Slackware).

## The debug hook

`/init` calls `debug_shell` at six points: after `debug_start`, after `find_data`, after
`persistent_changes`, after `mount_bundles`, after `union_append_bundles`, and after `fstab_create`.
Each drops to an interactive `ash` when `debug` is on the cmdline, letting you inspect exactly the
state described above at the moment it is produced.

It is the only supported way to see inside a failing boot, because `init_proc_sysfs` silences printk.

## What can go wrong, mapped to a stage

| symptom | stage |
|---|---|
| reboot loop with no output | 1, or a kernel without `CONFIG_IA32_EMULATION` — see [`initramfs.md`](initramfs.md) |
| `Cannot find Slax data` | 5 — wrong `from=`, or a storage driver missing from the initramfs |
| boots but changes never persist | 6 — no `perch` in the cmdline, or a read-only medium |
| a customization is ignored | 9 — bundle numbered too low, or placed in `/slax/` instead of `/slax/modules/` |
| `Setting up union using overlayfs` | 3 — `aufs.ko` did not load; `slax activate` will not work |
| `Can't find executable init command` | 13 — the bundle set has no `init` |
