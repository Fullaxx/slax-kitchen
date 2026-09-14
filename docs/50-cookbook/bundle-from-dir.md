# `bundle-from-dir` — pack a directory of your own files

**Status: verified** — applied on all four targets; the built bundle was unpacked and its contents
and modes checked against the source tree.

```sh
kitchen apply bundle-from-dir
```

```yaml
vars:
  bundle: 07-mytools
```

The offline equivalent of upstream's `dir2sb`, and the recipe to copy when you already have a tree
of files and want them on the live system.

## The contract

**The directory's contents become the root of the bundle.** `./usr/local/bin/foo` in your source
tree lands at `/usr/local/bin/foo` in the union — the source directory itself is not part of the
path. That is why `src:` points at a directory rather than at the files inside it.

```
bundle-from-dir.files/          ->   (root of the bundle)
├── etc/profile.d/slax-kitchen.sh    /etc/profile.d/slax-kitchen.sh
└── usr/local/bin/slax-sysinfo       /usr/local/bin/slax-sysinfo
```

`src:` is resolved relative to the recipe file, so a relative path travels with the recipe.

## Modes come from disk

The bundle preserves what it finds. `slax-sysinfo` is committed `0755` and arrives executable;
`slax-kitchen.sh` is `0644` and arrives non-executable. There is no `mode:` field to get wrong —
git tracks the executable bit, so `chmod +x` in your checkout is the whole mechanism.

Verified on a built bundle rather than assumed:

```
$ unsquashfs -d /tmp/x 07-mytools.sb && find /tmp/x -type f -printf '%M %p\n'
-rw-r--r-- /tmp/x/etc/profile.d/slax-kitchen.sh
-rwxr-xr-x /tmp/x/usr/local/bin/slax-sysinfo
```

If you generate the source tree with a script instead of committing it, set the modes there.

## The shipped example is deliberately tiny

Two text files. `ci/checks/00-no-binaries.sh` rejects binaries in this repository, and the point of
a template is its shape, not its payload. Point `src:` at your own tree and the recipe is finished;
nothing else needs to change.

The two files are still real rather than placeholders: `slax-sysinfo` prints what the running
system actually is (version, kernel, which bundles mounted, which union), which is genuinely useful
on an unlabelled machine that booted from a stick. `/etc/profile.d/*.sh` is sourced by both
flavours' `/etc/profile`, so it is the portable place to put shell-wide defaults.

## Numbering

`07` because the stock bundles occupy `01`–`06` and `savechanges` writes `99-changes-N.sb`.
Anything in `07`–`98` overrides the shipped system and is itself overridden by a saved session.
**Higher wins** — see [the anatomy notes on the union](../10-anatomy/union-and-persistence.md).

The verb refuses a name without an `NN-` prefix, because a bundle with no number has no defined
position in the stack and would silently never override anything.

## When to use something else

| you want | use |
|---|---|
| files on the live system with **no** squashfs rebuild | [`rootcopy-overlay`](rootcopy-overlay.md) |
| a published release tarball | [`bundle-from-tarball`](bundle-from-tarball.md) |
| distro packages and their dependencies | [`add-packages`](add-packages.md) |
| a couple of small config files written inline | [`branding`](branding.md) uses `bundle.files` |
