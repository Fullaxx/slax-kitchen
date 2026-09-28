# Publishing an image

The procedure for a project that publishes an ISO it built with slax-kitchen: what to run and
what travels with the image. A release of slax-kitchen itself attaches no image — see
[NOTICE.md](../../NOTICE.md) — so this is for the projects that use it.

Whoever publishes an image decides whether to, and nothing in this procedure refuses one.

## The procedure

```sh
sudo ./kitchen build myproject                         # out/myproject.iso + .provenance.json
sudo chown -R "$(id -u):$(id -g)" out                  # the build ran as root; out/ is root's
./kitchen sources out/myproject.iso \
    --markdown out/myproject.SOURCES.md --json out/myproject.sources.json   # optional
(cd out && sha256sum myproject.iso > SHA256SUMS)
```

Then a person uploads the image, `SHA256SUMS`, `myproject.iso.provenance.json` and, if they made
them, the two sources files — with `gh release create`, or however the project publishes. Nothing
here uploads.

**Several images of one version** — BIOS and UEFI variants, 32- and 64-bit — are the same commands
for each image, and one `SHA256SUMS` that lists them all:

```sh
(cd out && sha256sum myproject-bios.iso myproject-uefi.iso > SHA256SUMS)
```

A project that vendors the kitchen runs the same commands through `vendor/slax-kitchen/kitchen`.

## What travels with the image

| Asset | What it is |
|---|---|
| `<name>.iso` | the image; one per image |
| `SHA256SUMS` | checks every download. It does not promise that a rebuild matches: images are not byte-reproducible ([reproducibility](reproducibility.md)) |
| `<name>.iso.provenance.json` | what the build did: the base image, the kitchen and project commits with their submodule pins, every recipe applied, what each one fetched (URLs and sha256s) and which package versions apt resolved. A place on the build machine is written as `<work>`, `<kitchen>`, `<project>` or `<home>`. One per image |
| `<name>.SOURCES.md`, `<name>.sources.json` | optional: every file in the image, where it came from, and where its upstream publishes its source when that is known. One pair per image |

What the project changed is in its own repository and in this one, at the commits the provenance
records: its recipes, the files beside them, and the engine that ran them. No source is attached
for what the build did not change; `SOURCES.md` points at where each upstream publishes it —
linux-live and the repositories it names, `snapshot.debian.org` for each Debian source version, a
recipe's `upstream_source` for a download.

## Before the first publish

**Identity.** An image should not present itself as an official Slax release. Set the volume id
and branding with [`iso-identity`](../50-cookbook/iso-identity.md) and
[`branding`](../50-cookbook/branding.md).

## What this does not do

- **Decide whether an image may be published.** What is in an image and what its upstreams permit
  is the publisher's to weigh; [NOTICE.md](../../NOTICE.md) records what is known about Slax's own
  parts, its firmware included.
- **Reproduce a build.** The records explain an image, file by file. They do not make a second
  build byte-identical to the first.
- **Follow an image built on another project's image back to its origins.** `kitchen sources`
  matches files against the stock image of a known target, and a base another project built is
  not one, so it lists every file from that base as `base`, pointing at the base image by name
  and sha256. The base's own `SOURCES.md`, if its publisher made one, goes further. See
  [LAYERING.md](../../LAYERING.md#provenance-and-publishing).
