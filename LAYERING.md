# Layering

How one project builds on another project's work: slax-rpgs putting a game library on top of
slax-wine, which puts Wine on top of a stock Slax ISO. The decision this page records is that **a
project builds on the other project's released image, not on its source**. Every project vendors
this engine directly, and every build is an ordinary one-project build.

A *layer* here is a project in that chain. It is not a layer of the union filesystem — a bundle
in the running system's stack — which is what the word means everywhere else in these docs.

---

## The model

```text
stock Slax ISO --(slax-wine's recipes)--> slax-wine ISO --(slax-rpgs's recipes)--> slax-rpgs ISO
```

Each arrow is one build — `kitchen unpack`, `apply`, `pack` — by a project that vendors this
engine at `vendor/slax-kitchen`. The two arrows differ only in the image they start from.
`kitchen unpack` accepts any Slax image, including one this engine built
([bring your own ISO](docs/90-reference/cli.md#bring-your-own-iso)), and a profile names it with
`base.iso:`. A relative `base.iso:` is relative to the repository that holds the profile.

Nothing in the engine has to know that the base image was built by another project. Measured
2026-09-25: a small "platform" image was built from the stock 64-bit Debian ISO — a package
bundle at `20`, a release file at `21`, an application at `30` — and a second project built on
top of it with `kitchen build`, removing the application and adding a bundle of its own:

- `apply` reads flavour and arch from the tree it is given, so the profile's `base:` is held to
  the base image as it would be to a stock one.
- `pack` rebuilds `98-dpkg-db.sb` from the stock database plus every bundle's fragment, so the
  base's packages stay known: `600 from 05-chromium.sb + 1 fragment(s) -> 603 packages`.
- `kitchen diff base.iso consumer.iso --bundles` named exactly what the consumer did: its volume
  id, the bundle it removed, the bundle it added, and a `98-dpkg-db.sb` with identical content.

## Why not build on the base project's source

The other shape — slax-rpgs vendoring slax-wine, which vendors the engine, and rebuilding Wine
inside its own build — needs an engine that works two submodules down. At `b20e07e` it assumed
one project above it in three places:
[#43](https://github.com/Fullaxx/slax-kitchen/issues/43), a recipe's `requires` resolves against
the working directory; [#44](https://github.com/Fullaxx/slax-kitchen/issues/44), the engine reads
its operator configuration only from its own checkout; and
[#45](https://github.com/Fullaxx/slax-kitchen/issues/45), provenance records only the project
directly above the engine. It would also mean two pins that have to move in step. Building on the
image needs none of it. Those issues are the record of what building on the source would take, if
a project ever needs to change *how* the project below it builds rather than *what* it ships.

That is the cost: a consumer gets the base as built. It can remove the base's bundles and add its
own, but it cannot change the vars of the base's recipes. That is a change to the base project.

## What a base project owes the projects built on it

slax-wine is the first, and the examples are from its tree at
[`7535d6c`](https://github.com/Fullaxx/slax-wine/tree/7535d6c).

| | |
|---|---|
| **Versioned images with a sha256, and the engine commit that built them** | A consumer pins an image, not a commit, and pins the same engine unless it has a reason to differ: `kitchen pack` records it as `kitchen.commit` in `<image>.provenance.json`, and slax-wine's changelog names its pin. Until the base publishes an image, a consumer pins a local build, and only that machine has those bytes: [images are not byte-reproducible](docs/40-workflow/reproducibility.md). slax-wine's 1.0.0 was unreleased on 2026-09-25. |
| **A release file in the image** | So a build, and a person, can read which base they have. slax-wine writes `/etc/slax-wine-release`: `NAME`, `VERSION`, `BASE_ISO`, `BASE_SHA256`, `WINE` and `HOME_URL`. |
| **Its bundle numbers, split into platform and applications** | A consumer keeps the platform and replaces the applications. slax-wine uses `20`–`29` for its platform and `30`–`89` for applications ([ARCHITECTURE.md](https://github.com/Fullaxx/slax-wine/blob/7535d6c/docs/ARCHITECTURE.md)), and calls `30-notepadpp32` "the layer a games variant replaces" ([notepadpp32.md](https://github.com/Fullaxx/slax-wine/blob/7535d6c/docs/50-cookbook/notepadpp32.md)). |
| **What it applied, per image** | A consumer must not apply those recipes again, and the engine cannot warn it: the journal that records what ran lives in the base's work tree, not in the image. Every slax-wine image carries its boot-menu edit. What is written when an image is mastered is not on that list, because it does not carry over: identity, the UEFI boot entry and a hybrid MBR. Nor is `uefi-bootable`, though slax-wine's `-uefi` images carry it: a consumer lists it again, and it rebuilds the ESP the image came with. A consumer writes its own (steps 5 and 6 below). |
| **Platform recipes that carry only what every consumer needs** | A consumer inherits every file in the base image. slax-wine's `21-wine-desktop` also carries two product decisions, its release file and a mask for the browser its own profiles remove ([slax-wine#2](https://github.com/Fullaxx/slax-wine/issues/2)). Which project owns them is slax-wine's call. |

## What a consumer does

slax-rpgs is the first.

1. **Vendor this engine directly** at `vendor/slax-kitchen`, not through the base project, pinned
   at the commit the base release was built with unless there is a reason to differ.
2. **Pin the base image** by file name and sha256, check the sha256 before a build, and keep the
   image itself out of git: `isos/` in the project's `.gitignore`, as in this repository.
3. **Write a profile** whose `base:` names the Slax release underneath the base image, and whose
   `iso:` names the base image itself:

```yaml
apiVersion: slax-kitchen/v1
kind: Profile
metadata:
  name: myproduct
base: {flavour: debian, arch: 64bit, version: "12.2.0", iso: isos/slax64-wine-uefi-1.0.0.iso}
recipes:
  - name: remove-bundle
    vars: {drop: "^3[01]-notepadpp(32|64)\\.sb$"}
  - recipes/available/games.yaml
  - name: iso-identity
    vars: {volid: MYPRODUCT}
  - uefi-bootable
test: [structure]
```

4. **Remove first, then add.** [Removals come first](docs/40-workflow/composing-bundles.md#removes-come-first)
   in any plan; the consumer's own bundles then take the numbers it freed.
5. **Give the image its own identity.** Nothing of the base's identity carries over: the volume id
   and the other fields written when an image is mastered come from this build's recipes, and
   with none an image calls itself `slax`, as stock Slax does. Use
   [`iso-identity`](docs/50-cookbook/iso-identity.md) or a recipe of your own. The base's release
   file does stay in the image, and stays true of the base; write your own beside it.
6. **Do not apply what the base already applied**, except what is written when an image is
   mastered, which does not carry over. Identity is step 5. A hybrid MBR is a request `isohybrid`
   makes of `kitchen pack`, so list `isohybrid` whenever you want one. The UEFI boot entry is
   requested the same way, by `uefi-bootable`, which also builds the ESP, `boot/efi.img`, and
   mirrors the boot menu into GRUB. List it last, whichever image you build on, as the example
   does. On a UEFI image it replaces the ESP the base built: that rebuilds it from this build's
   menu, and is not a second application. It refuses an ESP it did not build (a signed loader,
   other loaders, another label) and says what it found, rather than lose it; build on the base's
   BIOS image then. Built on a UEFI image without it, the image ships the base's EFI partition
   with no entry pointing at it, and the structure test fails it.
   `kitchen unpack` records which of the two the base image had, and `kitchen pack` warns about
   each one this build will not write. A dropped MBR leaves nothing behind for a test to find.
7. **Build from the project's root:** `vendor/slax-kitchen/kitchen build profiles/myproduct.yaml`.
   `--keep` leaves the work tree for checks of the project's own.

## Bundle numbers

The engine's table is [which numbers are whose](docs/10-anatomy/bundles-squashfs.md#which-numbers-are-whose),
and it stays there. A project that others build on divides its part of that table into platform
and applications and says so in its own docs; a consumer replaces the applications part. The
numbers are advice. Nothing checks them, except that `98` and `99` are refused to every recipe in
every project.

## Gates

A gate checks the repository it lives in and nothing else. A project gets gates by copying them
from its engine pin, and chooses which: slax-rpgs, which will commit a game payload that
`00-no-binaries` refuses, can leave that folder out of its copy or leave the gate out. A copy
should carry a header naming the file and commit it came from, and be checked byte-for-byte
against the pinned engine at every update, as slax-wine's
[`96-release-consistency.sh`](https://github.com/Fullaxx/slax-wine/blob/7535d6c/ci/checks/96-release-consistency.sh)
does. A gate fix then reaches a project at its next engine update.

## The build driver

`kitchen build` has passed the volume id the recipes asked for to its structure test since
`6419fa4` (2026-09-17). #42 and slax-wine's
[D-13](https://github.com/Fullaxx/slax-wine/blob/7535d6c/docs/DECISIONS.md) say it never did; the
build measured above asserted the consumer's own volume id. A project needs a driver of its own
only for checks the engine cannot know about — slax-wine's exact module list, or its release file
read back out of the bundle — and that driver should call `kitchen` commands (`fetch`, `unpack`,
`apply`, `pack`, `test`) rather than anything inside the engine.

## Provenance and publishing

A consumer's `<image>.provenance.json` records the base image (name, sha256, size), the engine
commit, the consumer's own commit, and the recipes the consumer applied. The base's recipes are
in the base's own provenance, which travels with the base's release.

**Publishing a consumer's image through [the release procedure](docs/40-workflow/publishing-images.md)
is not supported yet.** `kitchen sources` accounts for a file by matching it against the stock
image of a known target, or against a recorded step of this build. A base image built by another
project is not a known target, so it has no stock image to match against: `sources` reports the
base as unknown and cannot account for any file that came from it, the stock Slax ones included,
and `ci/release-assets.sh` refuses. Teaching it to take a base release's own records is a
follow-up. For slax-rpgs, whether its game files may be redistributed at all comes first.

## Moving the pins

A consumer has two pins, and they move separately:

- **The engine**, the way slax-wine moves its own:
  [Moving the pin](https://github.com/Fullaxx/slax-wine/blob/7535d6c/docs/UPSTREAM.md#moving-the-pin).
- **The base image.** Read the base's changelog, check the new image's sha256, rebuild, and run
  `kitchen diff old.iso new.iso --bundles` to see what the base changed underneath you. Move the
  engine to the new release's `kitchen.commit` at the same time, unless there is a reason not to.

## Workarounds across projects

A project may work around a bug in the project below it as well as in the engine, so a marker names
the repository and not only the number: `WORKAROUND https://github.com/Fullaxx/slax-wine/issues/N`.
That is slax-wine's convention for the engine's issues already
([The lifecycle](https://github.com/Fullaxx/slax-wine/blob/7535d6c/docs/UPSTREAM.md#the-lifecycle)).

## Docs and the verification ladder

A base project's recipes are documented in the base project, and a consumer documents its own.
Each project claims the [rung](CONTRIBUTING.md#say-what-you-actually-verified) it reached on its
own images; a consumer inherits no rung from its base.

## Release identity

A consumer's release is its own version, the base release it was built on (name, version and
sha256), the engine commit, and the manifest of its own payload.

## Not settled here

Offered as follow-ups rather than decided:

- publishing a consumer's image: `kitchen sources` taking a base release's own records;
- a record inside the image of which recipes built it, so a consumer's `apply` could refuse to
  apply one again;
- engine gates that run over a project's own tree, so no project copies them;
- vars for `iso-identity`'s application id and preparer, and for what `boot-cmdline` appends.
  slax-wine writes its own identity recipe because those are fixed.

## How #42 was settled

[#42](https://github.com/Fullaxx/slax-kitchen/issues/42) listed eight places where the engine
assumed one project above it. With each project building on an image, and vendoring the engine
directly:

| #42 row | outcome |
|---|---|
| 1. `requires` resolves against the working directory (#43) | superseded: a consumer takes the base's image, not its recipes |
| 2. `vars:` on a profile entry named by path are dropped (#46) | fixed in `f2ea7d2` |
| 3. operator configuration is read only from the engine's checkout (#44) | superseded: every project vendors the engine directly, as slax-wine does |
| 4. provenance records one superproject (#45) | superseded: a build has one project above the engine again, and records the base image by sha256 |
| 5. `kitchen version` drops the commit inside a submodule | fixed in the commit that added this page |
| 6. `kitchen build` cannot pass the volume id | stale: it has since `6419fa4` |
| 7. gates are copied into every layer | a project copies what it needs from its own engine pin — [Gates](#gates) |
| 8. identity recipes are not parameterisable | a product writes its own identity recipe; vars are a follow-up |
