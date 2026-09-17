# Upstream's own `DOC/`, quoted

`vendor/linux-live/DOC/` — seven files, five of them tiny. Reproduced because they are short, they
are the author's own words, and two of them are the only upstream statement on their subject.

## `bundle.txt`

> A bundle is compressed squashfs filesystem, consisting of up-to-the-root directory structure. Old
> name for bundle is 'module'. Bundle format brings some new enhancements over the old modules.
>
> File extension for bundles may vary. Currently Slax uses .sb extension, where 'sb' means 'slax
> bundle'.
>
> How to create bundle:
> ```
> # mksquashfs bundle_rootfs wholefs.sb -comp xz -bs 1024k -Xbcj x86
> # mksquashfs /usr /usr.sb --keep-as-directory -comp xz -bs 1024k -Xbcj x86
> # . livekitlib; make_bundle bundle_rootfs wholefs.sb
> ```
>
> Special files in bundle filesystem structure:
> ```
>     /run/requires
>     /run/activate.sh
>     /run/deactivate.sh
>     /run/startcmd.sh ?
> ```

Two notes. The documented flag is `-bs 1024k`, but every actual call site uses **`-b 1024K`** —
`-bs` is not a real mksquashfs option, so the doc is wrong and the code is right. And
`make_bundle` does not exist in `livekitlib`; the function is called **`create_bundle`**.

The four `/run/` hooks are real and undocumented anywhere else.

## `terminology.txt`

> **Live Kit** — also known as Linux Live Kit. Formely known as Linux Live CD. Nowadays, people
> mostly use USB flash drives, cameras, and other devices to run such 'Live' linuxes, thus Live CD
> is no longer ideal name for it.
>
> Meaning of Kit is like a tool, toolkit, or such. Which (I believe) corresponds with the usage of
> such Live Linux distribution much better.
>
> **Bundles** — compressed squashfs images with some specialities.

## `boot_parameters.txt` — in full

> You can pass the following boot parameters:
>
> debug ... start shell prompt several times during live kit startup
>
> from=...  load data from given directory (search all drives for it)

That is the entire file. Two parameters. The real list is about fourteen, and the only authority for
it is `livekitlib` itself — which is why
[boot-modes-and-tricks](../05-using-slax/boot-modes-and-tricks.md) was derived from the code and
found that `nosound` is documented-but-dead while `load=` is implemented-but-undocumented.

## `supported_fs.txt`

> * iso9660 (CD) ..... using isolinux
> * FAT32 (vfat) ..... using syslinux or extlinux
> * ntfs ............. using syslinux or extlinux
> * ext2/3/4,btrfs ... using extlinux
> * any other fs ..... using lilo
>
> Most users will install on FAT32 for compatibility with the other operating systems (I mean
> Windows).

Worth reading against [persistence](../05-using-slax/persistence-perch.md): FAT32 is recommended
here for interoperability, and it is also the choice that forces persistence into a fixed-size
DynFileFS container rather than an unlimited bind mount.

## `sources.txt` — in full

> Source code for precompiled binaries (initramfs/static/*) can be found at
> http://ftp.slax.org/Slax-7.x-development/sources/Slax-7.0-sources/busybox-and-ntfs3g/

One URL, covering two of the nine binaries now in `initramfs/static/`, pointing at a Slax 7.x tree.
Slax 12.2.0 shipped eight of them: `mc` was added on 2023-10-30, three weeks after the release.

## `LICENSE` and `GNU_GPL`

`LICENSE` is 245 bytes:

> This software is released under GNU GENERAL PUBLIC LICENSE… This software is distributed with NO
> WARRANTY, use it at your own risk… written by Tomas M. <http://www.linux-live.org>

`GNU_GPL` is the full GPL v2 text, June 1991.

Because both live under `DOC/` rather than at the repository root, GitHub's licence detector reports
`null` for the project. The intent is unambiguous; it is simply not machine-detectable. See
[NOTICE.md](../../NOTICE.md).
