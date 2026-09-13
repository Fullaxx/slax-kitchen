# The union, and persistent changes

Two mechanisms that are usually discussed together because they meet at one directory:
`/run/initramfs/memory/changes`, the writable branch of the union. The union decides how the root
filesystem is assembled; persistence decides whether that branch survives a reboot.

## The union

### aufs, or overlayfs

```sh
modprobe aufs 2>/dev/null
if ! aufs_is_supported >/dev/null; then modprobe overlay 2>/dev/null; fi
```

aufs is preferred and is what every official image gets — the kernel is custom-built precisely
because aufs was never merged upstream (see
[`15-upstream/aufs-kernel.md`](../15-upstream/aufs-kernel.md)). overlayfs is a fallback for a kernel
without aufs.

The two are assembled differently:

```sh
# aufs — starts empty, with only the writable branch
mount -t aufs -o xino="/.xino",trunc_xino,br="$CHANGES" aufs "$UNION"
# then, per bundle:
mount -o remount,add:1:"$BUNDLE=rr+wh" aufs "$UNION"

# overlayfs — the whole stack declared at mount time
mount -t overlay overlay \
  -o lowerdir=$(find "$BUNDLES" -mindepth 1 -maxdepth 1 | sortmod | tac | tr '\n' ':' | sed -r 's/:$//'),\
upperdir=$CHANGES/changes,workdir=$CHANGES/workdir "$UNION"
```

Same precedence, opposite construction: aufs grows one branch at a time, overlayfs is told everything
up front. `sortmod | tac` reverses the order because overlayfs gives the **leftmost** `lowerdir` the
highest priority, whereas aufs gives it to the lowest branch index.

### What the difference costs

| | aufs | overlayfs |
|---|---|---|
| bundle precedence | higher number wins | same |
| `union_append_bundles` | does the work | **no-op** |
| `slax activate` / `deactivate` | works | **does not work** |
| whiteouts | `.wh.<name>` files, visible in `changes` | `char 0:0` device nodes |
| `xino` index | `/.xino`, `trunc_xino` | n/a |

`slax activate` adds a branch to a live union. overlayfs has no equivalent operation, so the command
cannot work there — it is not a missing feature in Slax, it is a kernel limitation.

**How to tell which you got:** the boot message is `Setting up empty union using aufs` or
`Setting up union using overlayfs`. From a running system, `grep aufs /proc/filesystems`.

This is the concrete risk in a `kernel.replace`: a stock distribution kernel has no aufs, so
replacing the kernel silently downgrades the union and disables runtime bundle activation, with no
error at any point.

### Whiteouts

Deleting a file that exists in a lower branch does not remove it — it cannot, the bundles are
read-only. aufs records the deletion as a `.wh.<name>` entry in the writable branch. Those entries
are real files in `memory/changes`, and they are meaningful: pack them into a bundle and that bundle
deletes the file for anyone who loads it.

This is how a high-numbered bundle can *remove* something rather than override it. `savechanges`
keeps them deliberately, so a saved session carries your deletions too; `kitchen`'s bundle builder
excludes them, because an offline package install has no legitimate reason to produce one and an
accidental whiteout would silently delete a shipped file.

aufs also keeps its own bookkeeping in the same directory — `.wh..wh.orph/`, `.wh..wh.plnk/`,
`.wh..wh.aufs`, `.wh..pwd.lock`. Those are *not* whiteouts and must never be packed; both
`savechanges` and `kitchen` drop them.

## Persistence — "perch"

**per**sistent **ch**anges. Off by default, and enabled by a substring match:

```sh
if grep -vq perch /proc/cmdline; then return; fi
```

That is a substring test against the whole command line, so `perchdir=new` and `perchsize=32GB`
each enable persistence on their own — there is no separate `perch` flag to remember.

**Booting from CD never has persistence.** `isolinux.cfg` sets no `perchdir=` and its two session
entries are `MENU DISABLED`; only `syslinux.cfg`, used from USB and disk, offers them. See
[`directory-map.md`](directory-map.md).

### Choosing a session

`restore_perch_session` reads `perchdir=`:

| value | |
|---|---|
| `resume` | the session named in `/slax/changes/last_session.txt` |
| `new` | a fresh numbered directory |
| `ask` | an `ncurses-menu` listing each session's age and size |
| `<N>` | session number `N` |
| `/dev/sdXN/path` | a session on a different device entirely |

Sessions are numbered directories under `<data>/slax/changes/`, plus `last_session.txt`.

### Two storage strategies

The filesystem decides, by a live test rather than by name:

```sh
touch "$T1" && ln -sf "$T1" "$T2" && chmod +x "$T1" && test -x "$T1" \
            && chmod -x "$T1" && test ! -x "$T1" && rm "$T1" "$T2"
```

Create a file, symlink it, set and clear the executable bit, check both took. If all of that works
**and** `device_bestfs` does not report ntfs:

```sh
mount --bind "$CHANGES/$PERCHDIR" "$2"
```

**A plain bind mount. No size limit, no container, no overhead** — the session directory simply
becomes the writable branch. This is the good path, and it is why a Linux-formatted stick is worth
the trouble.

NTFS is excluded even though ntfs3 passes the POSIX test, because its permission emulation is not
trustworthy enough to hold a root filesystem.

Otherwise — FAT32, NTFS, or anything that fails the test — Slax falls back to a container file:

```sh
@mount.dynfilefs -f "$CHANGES/$PERCHFILE" -s $PERCHSIZE -m "$2" -p 4000
mkfs.xfs.custom "$2/virtual.dat"          # only when creating
mount -o loop "$2/virtual.dat" "$2"
```

Three layers stacked on one mountpoint: DynFileFS presents a sparse virtual file, XFS is formatted
inside it, and the XFS is loop-mounted over the same path, hiding the DynFileFS mount beneath it.

| | |
|---|---|
| container | `<data>/slax/changes/<N>/changes.dat` |
| split size | **4000 MB, hardcoded** — FAT32 cannot hold a file ≥ 4 GiB, so it becomes `changes.dat`, `changes.dat.1`, … |
| default size | 16000 (≈16 GB) |
| minimum size | **16000** — a smaller `perchsize=` is silently raised |
| units | `perchsize=` accepts `G`, `GB`, `T`, `TB`; everything non-numeric is stripped |
| filesystem | XFS, via the bundled `mkfs.xfs.custom` |
| growing | only upward, only when `perchsize` > 16000, via `xfs_growfs` |

Sparse means the container costs only what is written, so the 16 GB floor is not 16 GB of disk up
front — but it does mean `perchsize=` is fixed at creation and can only be increased later.

### Why XFS inside a container

It is the one filesystem in the shipped set that grows online without unmounting, which is what makes
`perchsize=` raisable on a later boot. `xfs_growfs` and `mkfs.xfs.custom` are two of the seven static
binaries in the initramfs for exactly this.

## `savechanges` — the other kind of persistence

Perch keeps a live session on the medium. `savechanges` does something different: it packs the
current contents of `memory/changes` into a permanent bundle.

```sh
savechanges                                  # -> .../modules/99-changes-<next>.sb
savechanges /slax/modules/99-changes-1.sb    # or name it
```

99 outranks every shipped bundle, so the result behaves like a permanent overlay. It picks the next
free index automatically, remembering it in `/run/savechanges.session` so repeated saves in one
session overwrite rather than accumulate.

Four properties worth knowing:

- **It stages through a tmpfs** at `/tmp/changes$$`, so the whole changeset must fit in RAM.
- **It keeps whiteouts.** The exclusion regex drops only aufs's *internal* bookkeeping —
  `.wh..wh.orph/`, `.wh..wh.plnk/`, `.wh..wh.aufs`, `.wh..pwd.lock` — not ordinary `.wh.<name>`
  entries. So a file you deleted stays deleted for anyone who loads the resulting bundle. That is
  deliberate and often what you want, but it means a saved session can *remove* things as well as add
  them.
- **It drops every directory**, because `find` prints directories with a trailing `/` and the regex
  excludes `/$`. Parent directories are recreated by `cp --parents`, so only *empty* directories are
  actually lost.
- **It excludes** the per-boot churn: `var/{cache,backups,tmp,log}`, `var/lib/{apt,dhcp,systemd}`,
  `etc/{mtab,fstab,resolv.conf}`, `sbin/fsck.aufs`, `root/.Xauthority`, `root/.xsession-errors`,
  `root/.fehbg`, two Fluxbox state files, and the runtime directories `boot dev mnt proc run sys tmp`.

It also detects an overlayfs layout — `changes/` and `workdir/` and nothing else — and descends into
`changes/` automatically.

`kitchen`'s own bundle builder uses a similar but **not identical** exclusion list: it additionally
drops `.wh.*` entirely, because an offline package install has no legitimate reason to produce a
whiteout and an accidental one would silently delete a shipped file.

Offline, `kitchen`'s `bundle.packages` verb is the same idea applied to an ISO instead of a running
system: install into an unpacked bundle tree, diff, pack the delta as a new high-numbered bundle.

## Choosing

| you want | use |
|---|---|
| a stick that remembers everything, no size ceiling | Linux-formatted partition + `perchdir=resume` |
| a stick that also works as a Windows data drive | FAT32 + `perchsize=32GB` |
| changes baked into the image for others | `savechanges`, or build a bundle offline |
| files present at boot with no rebuild | `rootcopy/` — see [`runtime-layout.md`](runtime-layout.md) |
| a clean boot every time | CD, or simply omit `perch` |

The long-form user guide, including how to back up, move and delete sessions, is
[`docs/05-using-slax/persistence-perch.md`](../05-using-slax/persistence-perch.md).
