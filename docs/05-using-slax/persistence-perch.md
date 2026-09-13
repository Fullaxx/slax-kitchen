# Persistence — "perch"

**perch** = **per**sistent **ch**anges. This is the Slax feature people most often get wrong, so this
page is longer than the others.

By default every change you make lives in RAM and vanishes at shutdown. Persistence writes them to
the boot medium instead.

---

## ⚠️ The four things that surprise people

**1. It does not work from a CD.** Not "works badly" — the CD boot menu has no persistence entries
at all, and its two session options are greyed out. Optical media cannot be written to. Use a USB
stick or a hard disk.

**2. It is enabled by a substring match.** `livekitlib` literally tests whether the string `perch`
appears anywhere in the kernel command line. So `perchdir=` and `perchsize=` both enable it — and so
would any unrelated word containing "perch".

**3. On FAT32 or NTFS you get a fixed-size container, not free space.** More below; this is the big
one.

**4. `savechanges` is a different mechanism.** Persistence writes continuously to a session
directory. `savechanges` takes a snapshot into a permanent bundle. They solve different problems —
see [bundles-at-runtime](bundles-at-runtime.md).

---

## Turning it on

The stock USB/HDD boot menu already offers it:

| Menu entry | Parameter |
|---|---|
| Resume previous session | `perchdir=resume` |
| Start a new session | `perchdir=new` |
| Choose session during startup | `perchdir=ask` |

Or type it yourself: press `Esc` at the boot menu, `Tab` to edit, and add `perchdir=resume`.

| Value | Means |
|---|---|
| `resume` | continue the last session (default when `perchdir=` is bare) |
| `new` | start a fresh one, numbered one higher |
| `ask` | show a menu of existing sessions with their age and size |
| *a number* | resume that specific session |
| `/dev/sdb2/slax/changes` | put changes on a **different device or path** |

That last form is useful when booting from a read-only or nearly-full medium: it waits up to 20
seconds for the device to appear, mounts it, and uses it for changes.

---

## Sessions

Changes live in numbered directories under `/slax/changes/`:

```
/slax/changes/1/
/slax/changes/2/
/slax/changes/last_session.txt      <- which one to resume
```

`perchdir=new` creates the next number. `perchdir=ask` lists them with age and disk usage, which is
the easiest way to find and clean up an old one. To delete a session, delete its directory from
another machine — they are ordinary files.

---

## The filesystem matters enormously

This is the part worth understanding before you format a stick.

### On a Linux filesystem (ext4, xfs, btrfs) — unlimited

Slax tests whether the filesystem can do POSIX things — symlinks, and the executable bit surviving a
`chmod`. If it can, and the filesystem is not NTFS, the session directory is simply **bind-mounted**
as the writable layer.

Result: **no size limit**, native speed, files directly readable from any Linux machine. This is the
better setup by a wide margin.

### On FAT32 or NTFS — a fixed-size container file

FAT32 cannot store symlinks or permissions, so Slax cannot use it directly. Instead it creates:

```
/slax/changes/<session>/changes.dat
```

a **sparse, growable container** mounted through `dynfilefs`, formatted **XFS** inside, and
loop-mounted as the writable layer.

Three consequences:

| | |
|---|---|
| **Minimum and default size is ~16 GB** | `perchsize=` values below 16000 are raised to 16000 |
| **It is split at 4000 MB** | because FAT32 cannot hold a file over 4 GB — you will see `changes.dat`, `changes.dat.1`, … |
| **The size is fixed at creation** | `perchsize=` only matters the first time. Growing later needs `xfs_growfs`, which Slax does automatically only when you raise the number |

```
perchsize=32GB        # accepts G/GB/T/TB; decimal, so 32GB ≈ 32000
```

It is *sparse*, so a 16 GB container does not consume 16 GB up front — but the stick must have room
for it to grow into.

**If you have the choice, format the stick ext4.** You lose the ability to read it from Windows, and
gain unlimited, faster, simpler persistence.

---

## What actually gets saved

Everything written to the union: installed packages, config, your home directory. The writable layer
sits above every bundle, so a file you edit shadows the bundle's copy.

It does **not** capture anything under `/slax/` on the boot medium itself, or the contents of RAM.

## Checking it is working

```sh
df /                       # should show aufs
mount | grep memory        # /run/initramfs/memory/changes should be a real device, not tmpfs
```

If `changes` is tmpfs, persistence is off and you are running in RAM.

## Turning it off for one boot

Remove `perchdir=` from the command line, or boot the *Run Slax from RAM* entry. Add `toram` to
copy everything into memory and eject the medium; changes then definitely do not persist.

---

## With slax-kitchen

Nothing here changes stock behaviour. If you want persistence available from a CD-style image, apply
[`isohybrid`](../50-cookbook/isohybrid.md), `dd` it to a stick, and then note you still cannot
persist — a `dd`'d ISO is read-only. For persistence, use the `bootinst` route in
[install-to-usb](install-to-usb.md).
