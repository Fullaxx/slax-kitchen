# `livekitlib` — the 49 functions

`vendor/linux-live/livekitlib`, 1109 lines. Sourced twice for different purposes: by `build` at
build time, and — copied into the initramfs as `/lib/livekitlib` — by `/init` at boot.

Line numbers below are against the pinned copy and are stable.

## Boot-time, in execution order

This is the order `/init` calls them, which is the most useful way to read the file.

| Line | Function | What it does |
|---|---|---|
| 129 | `transfer_initramfs` | copy everything to a tmpfs at `/m` and `switch_root` into it, so `pivot_root` works later. Leaves `/lib/initramfs_escaped` as a sentinel |
| 146 | `init_proc_sysfs` | mount `/proc` and `/sys`, silence printk, `ln -s /proc/mounts /etc/mtab` |
| 185 | `init_devs` | modprobe `zram`, `loop`, `squashfs`, `fuse` |
| 223 | `init_aufs` | modprobe `aufs`; **fall back to `overlay` if absent** |
| 200 | `init_zram` | 512 MB zram swap, `swappiness=100` |
| 162 | `modprobe_everything` | modprobe every `.ko` in the initramfs, excluding network drivers |
| 300 | `init_blkid_cache` | prime the blkid cache from `/proc/partitions` |
| 598 | `find_data` | locate the Slax data — the heart of boot; see below |
| 663 | `check_data_found` | `fatal` if not |
| 787 | `persistent_changes` | set up perch; see [persistence](../05-using-slax/persistence-perch.md) |
| 924 | `copy_to_ram` | honour `toram`, then unmount the medium |
| 992 | `mount_bundles` | loop-mount every `.sb` |
| 239 | `init_union` | build the aufs union, or an overlayfs one |
| 1009 | `union_append_bundles` | insert each bundle as a branch |
| 888 | `copy_rootcopy_content` | `cp -a <data>/rootcopy/* <union>` |
| 1027 | `fstab_create` | write `/etc/fstab`, auto-mounting other disks if `automount` |
| 903 | `user_preinit` | **source** `<data>/rootcopy/run/preinit.sh` |
| 1064 | `change_root` | `pivot_root . run/initramfs`, then `exec chroot . init` |

## The four that decide everything

### `find_data` (598) and `find_data_try` (536)

Retries for 45 seconds, calling `refresh_devs` each second. For every device `blkid` reports —
skipping `/loop`, `/ram`, `/zram` — it mounts, looks for `$FROM/*.sb` **or** `$FROM/modules/*.sb`,
and keeps the first hit. Handles `from=` as a directory, a `/dev/X/path`, an **ISO file** (loop
mounted in turn), an `http://` URL, or `ask`.

### `init_union` (239) — where aufs and overlayfs diverge

```sh
if aufs_is_supported >/dev/null; then
   mount -t aufs -o xino="/.xino",trunc_xino,br="$1" aufs "$2"
else
   mount -t overlay overlay -o lowerdir=$(find "$3" -mindepth 1 -maxdepth 1 | sortmod | tac | tr '\n' ':' | sed -r 's/:$//'),upperdir=$1/changes,workdir=$1/workdir $2
fi
```

aufs starts **empty** and has branches added afterwards; overlayfs must be given the whole
`lowerdir` chain up front. That is why `union_append_bundles` does nothing under overlayfs, and why
`slax activate` cannot work there.

### `union_append_bundles` (1009) — why higher numbers win

```sh
find "$1" -mindepth 1 -maxdepth 1 | sortmod | while read BUNDLE; do
   mount -o remount,add:1:"$BUNDLE=rr+wh" aufs "$2"
done
```

`add:1:` inserts each branch at **index 1**, just below the writable branch at 0 — so each insertion
pushes the previous one down. Bundles are added in ascending numeric order, and each outranks the
last. **Higher number wins.** The overlayfs path reaches the same result by `sortmod | tac`, since
overlayfs gives the leftmost `lowerdir` the highest priority.

### `sortmod` (983)

```sh
cat - | sed -r "s,(.*/(.*)),\\2:\\1," | sort -n | cut -d : -f 2-
```

Prefixes the basename, sorts numerically, strips the prefix — so ordering follows the numeric
filename prefix regardless of directory depth. Gaps in the sequence are irrelevant.

## Build-time only

| Line | Function | Note |
|---|---|---|
| 103 | `allow_only_root` | `build` refuses to run otherwise |
| 117 | `create_bundle` | the canonical `mksquashfs` line (below) |

```sh
mksquashfs "$1" "$2" -comp xz -b 1024K -Xbcj x86 -always-use-fragments $3 $4 $5 $6 $7 $8 $9
```

The same flags appear in `dir2sb`, `savechanges` and both flavours' module builders — four call
sites, one format. Every stock bundle has superblock flags `0x04e0` as a result.

## Supporting cast

| Line | Function | |
|---|---|---|
| 11, 21, 59, 72 | `debug_start`, `debug_log`, `show_debug_banner`, `debug_shell` | the `debug` boot parameter |
| 32, 40, 46, 51 | `header`, `echo_green_star`, `log`, `echolog` | the green `*` boot messages |
| 81 | `fatal` | |
| 95 | `cmdline_value` | parse `key=value` from `/proc/cmdline` |
| 174 | `refresh_devs` | `mdev -s`, and set `/proc/sys/kernel/hotplug` |
| 214 | `aufs_is_supported` | greps `/proc/filesystems` |
| 262, 282 | `mounted_device`, `mounted_dir` | walk `/proc/mounts` upward |
| 313, 324, 347, 363 | `device_tag`, `device_bestfs`, `fs_options`, `mount_command` | **`device_bestfs` maps `msdos`/`fat` → `vfat` and `ntfs` → `ntfs3`** |
| 373–445 | `network_device`, `init_network_dev`, `init_network_ip`, `mount_data_http` | networking and httpfs2 |
| 474, 486 | `tftp_mget`, `download_data_pxe` | PXE, HTTP on port 7529 with TFTP fallback |
| 674 | `date_diff_since_now` | "3 days ago" in the session picker |
| 698 | `restore_perch_session` | session selection, incl. the ncurses menu |
| 955, 969 | `filter_load`, `filter_noload` | `load=` and `noload=`. **`noload` translates commas to `\|`; `load` does not** |
