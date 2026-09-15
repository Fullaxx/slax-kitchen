# `renumber-bundles` — change load order without rebuilding

**Status: matrix-verified** — applied on all four targets; `05-chromium.sb` renamed to `95-chromium.sb`
and the resulting ISO passes structure assertions.

```sh
kitchen apply renumber-bundles
```

```yaml
vars:
  match: "^05-chromium"
  to: "95"
```

## Renaming *is* the mechanism

**Load order is the numeric prefix, and higher wins** — because `union_append_bundles` inserts each
branch at aufs index 1, so the last one mounted ends up on top. A bundle's number is therefore its
priority, and changing the number is the entire operation.

Nothing is unpacked and nothing is recompressed. Renumbering an 80 MiB bundle costs a `rename(2)`.

## The example, and why it is the wrong direction on purpose

Moving Chromium from `05` to `95` puts it above `06-devel` and above anything you add in `07`–`89`.
Concretely: a file you ship in `07-mytools` **no longer wins** over Chromium's copy of the same
path.

That is usually *not* what you want. The useful direction is normally moving something **down** so
your own bundle overrides it. But `05 → 95` crosses every other bundle in the stack, which makes it
the clearest demonstration of what the number actually does.

## Shared prefixes are legal

Upstream ships **both** `01-core` and `01-firmware`. When two bundles share a prefix, `sort -n`
falls back to comparing the rest of the name, so `01-core` loads before `01-firmware`.

An exact filename collision is refused rather than silently clobbering one of them.

A single digit is zero-padded, so `to: 5` and `to: "05"` mean the same thing.

## Gaps are fine

Removing a bundle leaves `01,01,02,03,04` after dropping `05`, and that is completely fine —
`sortmod` sorts by the numeric prefix and does not care about gaps. **Do not renumber survivors to
"tidy" the sequence**; you would change their relative priority for no reason.

## The numbering convention

| range | who |
|---|---|
| `00`–`09` | the platform — upstream's `01`–`06`, plus recipes here that adjust the OS |
| `10`–`89` | **yours** |
| `90`–`97` | slax-kitchen's headroom — and where this recipe parks `05-chromium`. `97` is the highest a recipe may take |
| `98` | the generated package database — **refused**, `to: 98` is an error |
| `99` | `savechanges` — **refused**, `to: 99` is an error |

An earlier version of this table gave `07`–`98` to users. `98` is `kitchen pack`'s generated
database and `99` is where saved sessions land, and neither collision is merely an ordering
problem: [which numbers are whose](../10-anatomy/bundles-squashfs.md#which-numbers-are-whose) has
the measurements.

See [union and persistence](../10-anatomy/union-and-persistence.md) for what aufs is actually doing
underneath.
