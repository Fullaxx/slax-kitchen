# `bundle-from-tarball` — turn a release tarball into a bundle

**Status: matrix-verified** — downloaded, checksum-verified and packed on a 64-bit target
(which is its whole `compat:` range); the built
bundle was unpacked and the binary confirmed at `/usr/local/bin/fzf`.

```sh
kitchen apply bundle-from-tarball
```

```yaml
vars:
  bundle: 07-fzf
  version: "0.74.4"
  sha256: "05e6813a337cc722c3ed07e54a764b75cc5d671e2e60459db0ba696ee5fa7504"
```

Fetches a published tarball, verifies it, and packs it as a bundle. The example installs
[fzf](https://github.com/junegunn/fzf) — a fuzzy finder, genuinely useful on a live system where
you are usually hunting for a file on someone else's disk, and conveniently a single static binary
with no dependencies to reason about.

## The checksum

`sha256:` is optional, and this recipe gives one. Without it the verb unpacks whatever the server
serves that day, and `kitchen sources` marks the download as unpinned; nothing warns at build time.
This recipe pins because a bundle is code that runs as root on every boot of every image you hand
out. The hash is checked against the **archive as published**, which is the artifact upstream's own
release process produces.

A mismatch aborts the step and deletes the download; nothing half-fetched reaches the tree.

## `strip:` and `prefix:`

Two fields that between them handle almost every real tarball:

| | |
|---|---|
| `strip: 1` | drop the leading `name-version/` directory most archives are wrapped in |
| `prefix: /usr/local/bin` | place the contents somewhere other than the root of the union |

This particular archive has **no** leading directory — `tar tzf` shows exactly one member, `fzf` —
so there is nothing to strip. `prefix:` is what makes it land somewhere sensible: without it the
binary would end up at `/fzf` and the bundle would shadow the union's root directory entry.
`/usr/local/bin` is the right home for something the distribution did not ship, and it is already
on `PATH` on both flavours.

Absolute paths and `..` components inside the archive are **refused**, not sanitised — and so
are link *targets*, for symlinks and hardlinks alike. Checking the member's name alone is not
enough: extraction follows a symlink that is already on disk, so an archive shipping `x -> /etc`
and then `x/cron.d/kitchen` writes outside the tree entirely. Reported against `06c13bb` by the
slax-wine fork; see [SECURITY.md](../../SECURITY.md). A second report showed that fix was
incomplete — a two-member chain, where one symlink makes a later member resolve elsewhere — so
containment is now checked against the filesystem before each member is written.

Setuid and setgid members, device nodes and FIFOs are **refused**. This verb is `privilege: none`
and its output runs as root at boot; `bundle.script` is the route for content that needs them.

## A zip

The same verb takes a zip, which is how software for Windows, games especially, is usually
published. `strip:`, `prefix:`, the checksum and every refusal above apply to it member by member.
A zip made on a Mac usually carries a `__MACOSX/` tree of resource forks beside the payload, and
`strip: 1` re-roots that tree too, beside the payload. Measured on a 1,301-entry game archive
made that way: 649 of the 1,299 entries unpacked were that tree.

## Why this one is 64-bit only

This is the lesson, not a limitation of the verb. fzf publishes `amd64`, `arm64`, `armv5`, `armv6`,
`armv7`, `loong64`, `ppc64le`, `riscv64` and `s390x` — and **no i386 at all**. Upstreams drop
32-bit x86 routinely now.

So the recipe declares `arch: [64bit]`, and on a 32-bit target the recipe matrix reports it as a
**skip**, not a failure. When you adapt this recipe, check your source's architecture coverage
before assuming `arch: [32bit, 64bit]` — Slax still ships 32-bit images, and half your targets
going quietly missing is worth noticing.

## Cost

One download per build. `boot.payload` and `bundle.fromTarball` do not cache, so if you add several
tarballs you pay each fetch on every target — which in CI means four times. Prefer one archive with
several extractions over several archives where the choice exists.

Measured 2026-10-04: the download is fzf 0.74.4's tarball as served that day, and the bundle is
from the [recipe matrix](../60-testing/ci.md#recipe-sizes) on `debian-64bit-12.2.0`:

| | |
|---|---|
| download | 2.2 MiB |
| bundle | 1.6 MiB |
| files | 1 |
