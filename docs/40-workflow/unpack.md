# Unpacking an ISO

```sh
kitchen unpack isos/slax-64bit-debian-12.2.0.iso -o work
```

## Why xorriso's osirrox, not a loop mount or 7z

| Approach | Problem |
|---|---|
| `mount -o loop` | needs `CAP_SYS_ADMIN`. The dev container does not have it — see [container-vs-host.md](container-vs-host.md). |
| `7z x` | reads ISO9660 fine, but **discards Rock Ridge permission bits**. `bootinst.sh` and `extlinux.x*` would come back mode `0644` and the rebuilt ISO would ship a non-executable boot installer. |
| `xorriso -osirrox on` | restores Rock Ridge names *and* modes, needs no privileges. **This is what we use.** |

Verified after extraction:

```
755 work/iso/slax/boot/bootinst.sh
755 work/iso/slax/boot/extlinux.x64
644 work/iso/slax/boot/isolinux.cfg
644 work/iso/readme.txt
```

which matches the `PX` entries in the source ISO's directory records exactly.

## What you get

```
work/
  iso/                     the full ISO tree, editable
    readme.txt
    slax/
      boot/                bootloaders, kernel, initramfs  (31 files)
      modules/             the .sb bundles                  (6 or 7 files)
      changes/             empty on a stock ISO
  .kitchen/
    origin.yaml            provenance: source path, sha256, size, timestamp
```

`origin.yaml` is what lets `kitchen pack` and `kitchen diff` reason about where the tree came from,
and what `kitchen probe` compares against `compat/`.

## Starting over

`.kitchen/` fills up as you work: `apply` adds `journal.yaml` and `provenance.json`, and
`pack.yaml` once a recipe asks something of `pack` — the record of what was applied to this
`iso/`, what that fetched and built, and how `pack` is to master it. That record describes this
tree and no other, so it goes with the tree. Unpacking into a work tree that exists is refused —
its `iso/`, or a `.kitchen/` left behind after `iso/` was deleted — and `--force` replaces both.
`status`, `apply` and `pack` recognise a `.kitchen/` with no `iso/` beside it too, and name the
`unpack --force` that starts over — with the image the tree was unpacked from, when `origin.yaml`
records one that is still there and unchanged (the sha256 it recorded), and its paths quoted, so
it can be pasted as it stands.

Note there is no `slax/rootcopy/` on a stock ISO — the initramfs looks for one and copies its
contents onto the union at boot, but upstream ships none. Creating it is the cheapest
customization available; see the `rootcopy-overlay` recipe.
