# 30 · Software inventory

Everything that ships inside a Slax ISO, from the kernel down to the individual packages. Where
[`10-anatomy/`](../10-anatomy/) explains the *shape* of the image, this section is the *contents*.

| | |
|---|---|
| [`kernel.md`](kernel.md) | 6.1.38, why it is custom, and the one config symbol you must not drop |
| [`initramfs-userland.md`](initramfs-userland.md) | busybox 1.26.2, the seven helpers, the module set |
| [`bundle-map.md`](bundle-map.md) | which bundle holds what, and the cumulative package database |
| [`debian-12.2.0.md`](debian-12.2.0.md) | 600 packages, systemd, ConnMan |
| [`slackware-15.0.4.md`](slackware-15.0.4.md) | 423 packages, sysvinit, and `06-devel` |
| [`slax-tooling.md`](slax-tooling.md) | the 14 helper commands Slax adds, and which flavour has which |
| [`known-upstream-bugs.md`](known-upstream-bugs.md) | twelve issues found during analysis |
| [`manifests/`](manifests/) | the generated inventories these pages are written from |

## Start here

[`known-upstream-bugs.md`](known-upstream-bugs.md) is the highest-value page in the repository if you
are about to build something. It is short, and every entry is a thing that will otherwise cost you an
afternoon.

## Regenerating

```sh
ci/gen-manifests.sh isos/slax-*.iso
```

Three seconds for all four images, read-only and unprivileged: bundles are read in place with
`unsquashfs -o <offset>` rather than extracted. Committing the diff is how a new upstream release
gets adopted.

Run it as root — the initramfs contains seven device nodes, and a non-root `cpio` turns them into
empty regular files.
