# `config` — build-time settings

`vendor/linux-live/config`, 47 lines, all comment and assignment. Sourced by `build`, by
`initramfs_create`, **and — copied verbatim into the initramfs as `/lib/config` — by `/init` at
boot.** So the same file configures the build and the running system.

| Variable | Upstream default | In Slax | Meaning |
|---|---|---|---|
| `LIVEKITNAME` | `"linux"` | **`"slax"`** | the directory on the medium, i.e. `/slax/` |
| `VMLINUZ` | `/vmlinuz` | same | kernel to copy into the image |
| `KERNEL` | `$(uname -r)` | same | which `/lib/modules/` tree to pull from |
| `MKMOD` | `bin etc home lib lib64 libx32 opt root sbin srv usr var` | same | top-level dirs that become `01-core.sb` |
| `NETWORK` | `false` | **`true`** | include ethernet drivers in the initramfs |
| `LIVEKITDATA` | `/tmp/$LIVEKITNAME-data-$$` | same | staging directory |
| `BEXT` | `sb` | same | bundle extension |
| `LMK` | `lib/modules/$KERNEL` | same | kernel module path |

## The Slax delta is exactly two lines

Both flavours' `build` scripts patch the file in place before using it:

```sh
sed -i -r 's/^LIVEKITNAME.*/LIVEKITNAME="slax"/' $THIS/../../config
sed -i -r 's/^NETWORK.*/NETWORK=true/' $THIS/../../config
```

That is the entire difference between the file in this repository and the one inside a shipping
`initrfs.img` — confirmed by hashing both.

## ⚠️ `LIVEKITNAME` is not just a variable

Upstream's own comment:

> If you change it, you must run `./tools/isolinux.bin.update` script in order to update
> `isolinux.bin` for CD booting. If you do not need booting from CD … then you can ignore
> recompiling `isolinux.bin`, just rename `LIVEKITNAME` and you're done.

The directory name is **compiled into `isolinux.bin`**. Renaming `/slax/` without re-patching that
binary produces an ISO that boots from USB but not from CD. See [tools](tools.md).

`build` also rewrites the string through `sed` in three other places — `syslinux.cfg`,
`bootinst.bat`, and the EFI config — rather than templating it, so a rename touches four mechanisms.

## `MKMOD` is a denylist by omission

There is no include list of packages. Everything under those top-level directories on the build
machine ends up in `01-core.sb`. Upstream's comment notes only:

> No subdirectories are allowed, no slashes, so You can't use `/var/tmp` here for example.
> Exclude directories like proc sys tmp

Which is why `01-core.sb` ships **no** `/tmp`, `/proc`, `/sys`, `/dev`, `/run`, `/media`, `/mnt` or
`/boot` — `change_root()` creates them at boot instead. That absence surprises anything that treats
a bundle as a complete root filesystem; see [edit-bundles](../40-workflow/edit-bundles.md).
