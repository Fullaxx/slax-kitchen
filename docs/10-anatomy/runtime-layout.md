# `/run/initramfs/memory/` — the runtime map

If you read one page in this section, read this one. Almost every practical question about Slax —
where do my bundles live, where did my changes go, how do I get at the boot medium, why can I eject
the stick — resolves to a path under here.

## How it comes to exist

The initramfs does its work under `/memory`. `change_root` then pivots, which moves the whole
initramfs (and `/memory` with it) to `/run/initramfs`:

```sh
mount -t tmpfs tmpfs run
mkdir -p run/initramfs
pivot_root . run/initramfs
```

So every path below has two names depending on when you ask: `/memory/X` during boot, inside `/init`
or a `preinit.sh` hook, and `/run/initramfs/memory/X` afterwards, from the running system.

## The map

```
/run/initramfs/                 the initramfs itself, kept alive for shutdown
├── shutdown                    run on the way down — see shutdown.md
├── bin/                        busybox + the 7 static helpers, still available
└── memory/
    ├── data      → the boot medium, mounted. Contains slax/
    ├── bundles/  → one directory per .sb, each a read-only squashfs loop mount
    │   ├── 01-core.sb/
    │   ├── 01-firmware.sb/
    │   └── …
    ├── changes   → the writable branch of the union
    ├── union     → the merged root — i.e. what / is
    ├── toram     → only with toram: a copy of data, in tmpfs
    ├── iso       → only when booted from an ISO file: the loop-mounted image
    └── modules   → RAM store for bundles added by `slax activate`
```

## Each entry, and what it is good for

### `memory/data`

The boot medium as mounted by `find_data`. On a USB stick this is a real, writable filesystem: this
is where `/slax/changes/` sessions are written, where you would drop a new bundle to have it load at
next boot, and where `rootcopy/` goes.

```sh
ls /run/initramfs/memory/data/slax/modules/
cp 07-mytools.sb /run/initramfs/memory/data/slax/modules/
```

On CD it is read-only, and with `toram` it is **not mounted at all** — `copy_to_ram` unmounts it
after copying. That is the whole point of `toram`, and also why "just copy a bundle onto the stick"
stops working in that mode.

### `memory/bundles/`

One directory per bundle, each a read-only squashfs loop mount named after the file. Useful for
answering "which bundle does this file come from?", which the union itself cannot tell you:

```sh
for b in /run/initramfs/memory/bundles/*/; do
  [ -e "$b/usr/bin/chromium" ] && echo "$b"
done
```

### `memory/changes` — the writable branch

Every write to `/` lands here, and this is the single most useful directory on a running Slax. What
it *is* depends on how you booted:

| boot | `memory/changes` is |
|---|---|
| CD, or no `perch` | tmpfs — lost at poweroff |
| USB on a Linux filesystem | a bind mount of `/slax/changes/<N>/` on the medium |
| USB on FAT/NTFS | an XFS filesystem inside `changes.dat`, via DynFileFS |

Because it is the top branch of the union, its contents are exactly your delta from the shipped
image:

```sh
find /run/initramfs/memory/changes -type f | head
```

That is the same set `savechanges` packs into a `99-changes-N.sb`. Under aufs you will also see
`.wh.*` whiteout entries marking deleted files; they are meaningful, and they are why
`kitchen`'s bundle-building excludes them.

### `memory/union`

The merged root. `/` and `/run/initramfs/memory/union` are the same filesystem — which sounds
useless until you are inside a `preinit.sh` hook, where the pivot has not happened yet and the union
is only reachable by this path. That is why `user_preinit` is passed it as `$1`.

### `memory/toram`

Present only with `toram`. A full tmpfs copy of the data directory, which is why `toram` needs RAM
greater than the size of the bundles you are loading. After the copy the medium is unmounted and can
be removed.

### `memory/iso`

Present only when `from=` named an ISO **file** rather than a device or directory — the ISO is
loop-mounted here and `data` points inside it. With `toram` the initramfs unmounts both this and the
filesystem the ISO file lives on.

### `memory/modules`

Where `slax activate` stages a bundle loaded at runtime. It is in RAM, so activations do not survive
a reboot unless the bundle is also copied to `memory/data/slax/modules/`.

`slax activate` works by adding a branch to the live aufs union, which is why it is **aufs-only**:
under overlayfs the branch set is fixed at mount time and cannot be extended.

## Practical recipes

**Add a bundle for the next boot** (USB, not `toram`):

```sh
cp 07-mytools.sb /run/initramfs/memory/data/slax/modules/
```

**Add one right now**, without rebooting:

```sh
slax activate 07-mytools.sb
```

**See what you have changed this session:**

```sh
find /run/initramfs/memory/changes -type f -not -name '.wh.*'
```

**Find which bundle owns a file:**

```sh
ls -d /run/initramfs/memory/bundles/*/usr/bin/chromium 2>/dev/null
```

**Rebuild an ISO from the running system** — `genslaxiso` reads exactly this tree, which is why it
only works on a booted Slax. `kitchen pack` is the offline equivalent; see
[`docs/40-workflow/repack-iso.md`](../40-workflow/repack-iso.md).

## Why the initramfs is still mounted

`pivot_root` moves it rather than discarding it, so `/run/initramfs/shutdown` survives to run at
poweroff. systemd looks for that path by convention; Slackware's `rc.6` checks for it explicitly.
Both are covered in [`shutdown.md`](shutdown.md).

A side effect worth knowing: **busybox and the seven static helpers are still on disk at
`/run/initramfs/bin/`** after boot. If you need `@mount.dynfilefs` or `mkfs.xfs.custom` from the
running system, that is where they are — they are not in any bundle.
