# `bundle-from-url` — one pinned download into a bundle

**Status: matrix-verified** — built and structurally asserted on all four targets with
`ci/recipe-matrix.sh` (2026-09-27); the built bundle was unpacked and the binary confirmed at
`/usr/local/bin/jq`, root-owned and `0755`, and `kitchen sources` listed it as a download.

```sh
kitchen apply bundle-from-url
```

```yaml
vars:
  bundle: 17-jq
  version: "1.8.2"
  sha256_64bit: "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f"
  sha256_32bit: "ba996e8ce436973e2f39e2639405a37e8c81ba8c722b71c83996278ad0af16dd"
```

Puts one file that is not an archive — here [jq](https://github.com/jqlang/jq), a single statically
linked binary — into a bundle, fetched and checked by the engine. It is `privilege: none`: no
chroot, no build root, no downloader in the image.

## A `url:` entry

```yaml
- verb: bundle.files
  bundle: 17-jq
  files:
    - dest: /usr/local/bin/jq
      url: https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64
      sha256: "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f"
      upstream_source: https://github.com/jqlang/jq/tree/jq-1.8.2
      mode: "0755"
```

The engine downloads the file and **refuses it unless its sha256 is the one pinned** — the step fails
naming the URL and both hashes, and no bundle is built. The pin and `upstream_source:` are both
optional on a `url:` entry ([#62](https://github.com/Fullaxx/slax-kitchen/issues/62)); this recipe
gives both, because a pinned download is one a rebuild can repeat. The file's mode is `0644` unless
the entry says otherwise, and setuid and setgid are refused, as for every `bundle.files` entry.

`kitchen sources` then lists the file as a **prebuilt part** of the bundle: a download installed
unmodified, with the URL it came from, its sha256 and where its upstream publishes the source. The
bundle itself is the recipe's.

A `url:` entry can sit beside `content:` and `src:` entries in the same step. Downloads are placed
after the others, and one that lands on a path another entry wrote is refused.

## Why not the other routes

| route | what it costs |
|---|---|
| [`bundle.fromTarball`](bundle-from-tarball.md) | takes only an archive |
| `boot.payload` | writes only under `slax/`, never into a bundle |
| a `bundle.script` that downloads and prints `KITCHEN-FETCHED` | `privilege: chroot`, the whole stack unpacked as the build root, network inside it, a downloader in the image — and on Slackware a certificate bundle first |
| staging the file where git ignores it, and `bundle.files` `src:` | a build step of the project's own to fetch the file first, and a pin only if that step checks one; `kitchen sources` lists it, pointing at its source if the entry gives `upstream_source:` |

[A file the build downloads](../90-reference/verbs.md#a-file-the-build-downloads) has the whole
comparison. Measured for #59 with a Notepad++ installer: the image built with the `bundle.script`
route took 9 s, and the same image with a one-file `bundle.files` step took 2 s.

## Both arches, one step each

jq publishes a static binary for `amd64` and for `i386`, so the recipe covers all four targets —
two steps, one per arch, each guarded by `when:`. Only one runs, so the bundle is produced once.
Check your own source before copying that: [`bundle-from-tarball`](bundle-from-tarball.md)'s fzf
publishes no i386 at all.

## Cost

One download per build — `url:` entries are not cached — and in CI it is built weekly rather than on
every push, since github.com is outside this repository; the fetch itself is tested in-process on
every push. See [`ci/slow-recipes.txt`](../../ci/slow-recipes.txt).

| | |
|---|---|
| download | 2.2 MiB (amd64), 2.1 MiB (i386) |
| bundle | 708 KiB (amd64) |
| files | 1 |
