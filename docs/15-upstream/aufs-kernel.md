# Why the kernel is custom

`vendor/linux-live/Slax/debian12/aufs-kernel-compile/compile`, plus `config-i686` and
`config-x86_64`.

Slax does not ship a distribution kernel. It ships one built specifically to carry **aufs**, which
has never been merged into mainline.

## The build

```sh
KERNELVERSION=6.1
KERNEL=linux-source-$KERNELVERSION
AUFS=aufs5-standalone
CHECKOUT=origin/aufs6.1
```

```sh
apt install build-essential $KERNEL bc kmod cpio flex libncurses5-dev libelf-dev \
            libssl-dev dwarves bison git rsync python3 openssl
cd /usr/src && tar -xf $KERNEL.tar.xz && cd $KERNEL
cat $CWD/$CONFIG > .config
git clone https://github.com/sfjro/$AUFS && git checkout $CHECKOUT
cp -aR $AUFS/fs . ; cp -a $AUFS/include/uapi/linux/aufs_type.h include/uapi/linux
patch -p1 < $AUFS/aufs6-kbuild.patch
patch -p1 < $AUFS/aufs6-base.patch
patch -p1 < $AUFS/aufs6-mmap.patch
patch -p1 < $AUFS/aufs6-standalone.patch
patch -p1 < $AUFS/aufs6-loopback.patch
```

Five patches against the tree, plus two more (`vfs-ino`, `tmpfs-idr`), then `make bindeb-pkg`.

Note the repository is named `aufs5-standalone` but the branch is `origin/aufs6.1` — the project
kept the old repository name across kernel generations.

## What ships

Both flavours ship the **identical kernel binary**, confirmed by hashing `vmlinuz` from all four
ISOs. It is a Debian-toolchain build even on Slackware:

```
Linux version 6.1.38 (root@slax) (gcc (Debian 12.2.0-14) 12.2.0,
GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Fri Aug 11 19:41:11 UTC 2023
```

The 32-bit build carries `LOCALVERSION=-smp`, so its release is **`6.1.38-smp`** and its module
directory is `/lib/modules/6.1.38-smp`. Anything hardcoding `6.1.38` breaks on 32-bit.

## Config options that matter

From `config-x86_64`:

| | Why |
|---|---|
| `CONFIG_AUFS_FS=m` | the union filesystem Slax is built on |
| `CONFIG_AUFS_BRANCH_MAX_32767=y` | branch limit — one per bundle |
| `CONFIG_AUFS_EXPORT=y`, `CONFIG_AUFS_INO_T_64=y`, `CONFIG_AUFS_XATTR=y` | |
| `CONFIG_NTFS3_FS=m` | `device_bestfs` maps ntfs → ntfs3 |
| **`CONFIG_IA32_EMULATION=y`** | **the entire initramfs is 32-bit i386 binaries** |

That last one is easy to miss and fatal to get wrong. Every static binary in `initrfs.img`, busybox
included, is i386 — so an x86_64 kernel without IA32 emulation cannot execute `/init` at all.

## Consequences for replacing the kernel

A stock distribution kernel has no aufs. `init_aufs` falls back to overlayfs, which **works** —
Slax boots — but silently loses:

- `union_append_bundles`, which is inside `if aufs_is_supported`;
- `slax activate` / `deactivate`, which use `remount,add:`/`del:` and parse `/sys/fs/aufs/`;
- the ability to add a branch to a running union at all.

And if the replacement lacks `CONFIG_IA32_EMULATION`, it does not boot. The `kernel.replace` verb is
written but must warn about both.
