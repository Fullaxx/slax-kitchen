# Publishing an image

The procedure for a project that publishes an ISO it built with slax-kitchen: what to run, what
travels with the image, and what each step refuses. A release of slax-kitchen itself attaches no
image — see [NOTICE.md](../../NOTICE.md) — so this is for the projects that use it.

This page describes what the tooling does. It is not legal advice, and whoever publishes an image
decides whether to.

## The procedure

```sh
sudo ./kitchen build myproject                       # out/myproject.iso + .provenance.json
./kitchen sources out/myproject.iso                  # every file accounted for, or exit 1
ci/release-assets.sh out/myproject.iso out/release   # the set that travels with the image
ci/release-verify.py out/release                     # check the set before anything leaves
ci/redistribution-claim.py out/release > redistribution.md
```

Then a person uploads `out/release/*` — with `gh release create`, or however the project publishes.
Nothing here uploads.

A project that vendors the kitchen runs the same scripts from `vendor/slax-kitchen/ci/`. The project
is found as the kitchen's git superproject (or named by `PROJECT_ROOT`), and its tree is archived
alongside the kitchen's.

| Step | Refuses |
|---|---|
| `kitchen build` | nothing new; `kitchen pack` writes `<iso>.provenance.json` beside the image |
| [`kitchen sources`](../90-reference/cli.md#sources-iso---json-f---markdown-f---fetch-dir) | a file whose sha256 matches neither the stock image nor a recorded step; an image built from a dirty checkout; a file a recipe copied in that its commit does not hold |
| `ci/release-assets.sh` | everything `sources` refuses; a kitchen or project checkout with uncommitted changes; an asset name GitHub would rename; a non-empty output directory |
| `ci/release-verify.py` | see below |

`release-verify.py` exits 1, naming every problem, when:

- `SHA256SUMS` does not list exactly the files present, or a hash is wrong
- the provenance, the sources manifest and the index do not describe the same image
- anything is unresolved, or a recipe marked `redistribution: {allowed: false}` is in the image
  (`all-browsers` is)
- something built here has no source among the assets
- a project archive is missing a submodule, or holds different pins than the build recorded
- an asset is 2 GiB or more, or there are more than 1000 (GitHub's limits)
- any record names a place inside this machine's kitchen or project checkout, or its home
  directory. The rule is where the machine really is, not what a path looks like: an image's
  own `/root/...` is ordinary.
- an attached image carries Slax's firmware bundle without the copyright files of its Debian
  firmware packages

With `--assert-no-images` it also fails if any asset *is* an ISO 9660 image, a squashfs, an ELF
or PE executable, or a FAT filesystem, judged by its bytes rather than its name. CI uses that for an
image it builds and must not publish; see [below](#the-image-ci-builds-and-never-publishes).

## What travels with the image

| Asset | What it is |
|---|---|
| `<name>.iso` | the image |
| `SHA256SUMS` | checks every download. It does not promise that a rebuild matches: images are not byte-reproducible ([reproducibility](reproducibility.md)) |
| `<name>.iso.provenance.json` | what the build fetched and built: URLs and sha256s, package versions, the build host's GRUB and MBR, the kitchen commit and submodule pins |
| `<name>.SOURCES.md`, `<name>.sources.json` | every file in the image, classified, and where the source of each part is published |
| `slax-kitchen-<commit>-source.tar.gz` | the kitchen tree at the recorded commit, **with its submodules**. GitHub's automatic source archives leave submodules out |
| `<project>-<commit>-source.tar.gz` | the same for the project that vendors the kitchen |
| `<source>_<version>.source.tar` | the source package of something built here, such as GRUB's EFI image |
| `busybox-<version>.tar.bz2`, `….config` | a build claim's source and configuration, when the image replaces busybox |
| `release-index.json` | every asset's role, sha256 and size, and what it is the source of |

Source is attached for what the build compiled, built or modified. Everything included as its
upstream published it — Slax's own parts, Debian packages, a vendor's tarball — is listed in
`SOURCES.md` with the place that upstream publishes its source: linux-live and the repositories it
names, `snapshot.debian.org` for each Debian source version, the recipe's `upstream_source` for a
download.

**What weakens a pointer is said, not smoothed over.** A download with no `upstream_source`, and one
the recipe pinned no sha256 for — what it fetched is what that server served that day — are
warnings, and `kitchen sources --strict` makes them unresolved instead. A bundle a script wrote with
network access carries that fact, because what came over the connection is the script's own account.
A repacked initramfs is opened and its members counted against the stock manifest, so "the parts
that are Slax's are Slax's" is a count rather than a claim.

## Before the first publish

**Identity.** An image must not present itself as an official Slax release. Set the volume id and
branding with [`iso-identity`](../50-cookbook/iso-identity.md) and
[`branding`](../50-cookbook/branding.md).

**Trademarks.** Software a recipe adds can carry trademark rules of its own. The Tor Project's
policy does not allow "Tor" in the name of another product without written permission, which is why
the image below is never published.

**Firmware.** Using an image that contains firmware implies acceptance of each firmware's license
terms. Slax's own build removed the license texts from its firmware bundle, except
`ipw2x00.LICENSE`. [`firmware-refresh`](../50-cookbook/firmware-refresh.md) reinstalls Debian's, and
brings a license file with every file it copies from linux-firmware. To leave firmware out instead,
see [building without firmware](../50-cookbook/remove-bundle.md#building-without-firmware).
`release-verify.py` refuses an attached image whose Debian firmware packages lack their copyright
files. The Broadcom b43 files in Slax's bundle stay either way: no license text came with them, and
`SOURCES.md` and the release notes say so.

**Where the notes come from.** `ci/redistribution-claim.py` writes the notes' Redistribution section
from the verified directory, so it names what is attached and cannot promise anything that is not.
`RELEASE_ASSETS=out/release ci/release-notes.sh <tag>` uses it in place of the kitchen's own "no
image is attached" section.

## The image CI builds and never publishes

The weekly CI run builds the `tor` profile and runs the whole procedure on it —
`release-assets.sh --no-image`, then `release-verify.py --assert-no-images` — and uploads the
verified records and source as a workflow artifact. The image itself is not in the set, and the
check that it is not reads content, so renaming it would not get it through. It proves the pipeline
works on a real profile with a download, packages and a UEFI build, without publishing an image that
carries another project's trademark. See [ci.md](../60-testing/ci.md#tor-assets-the-pipeline-proven-weekly).

## What this does not check

- **That an upstream still serves its source.** The pointers are permanent archives where one exists
  (`snapshot.debian.org`, Launchpad), and were checked when the code that writes them was.
  `kitchen sources` is offline and does not fetch them.
- **Licenses.** It records what is in the image and where the source is. It does not decide whether
  a license permits publishing — a recipe says so with `redistribution:`.
- **A rebuild.** The records explain an image, file by file. They do not make a second build
  byte-identical to the first.
