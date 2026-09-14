# Security

## Reporting

Please **do not open a public issue** for a vulnerability. Use GitHub's private reporting —
**Security → Report a vulnerability** on this repository — or contact the maintainer directly
through their GitHub profile.

Include: what you found, how to reproduce it, and which of the four targets you saw it on. If it
involves a built ISO, the output of `kitchen probe` on that ISO pins down exactly what you built.

## What is in scope

This project builds **bootable images that run as root**, so the interesting surface is not the
build tool so much as what it produces.

In scope:

- Anything that makes `kitchen` write outside the tree it was pointed at, or execute code from a
  recipe in a way a reader of that recipe would not expect. Recipes are meant to be **shared**, so
  a recipe that can do more than it appears to is a real finding.
- Anything that lets a downloaded artifact through without its pinned checksum being enforced.
- Anything that weakens a built image relative to what the recipe says it does — a credential that
  is more permissive than documented, a service enabled that should not be, a trust store that
  accepts more than it should.
- Our CI and release path.

Out of scope, because it is documented behaviour rather than a flaw:

- **Stock Slax logs in as `root` / `toor`, and prints so on tty1.** That is upstream's design for a
  live system. `users-and-auth` changes it.
- **Recipes marked `privilege: chroot` run arbitrary code by design** — `bundle.script` and
  `bundle.packages` exist to do that. Running an untrusted recipe is equivalent to running an
  untrusted script.
- **Secrets baked into an image are published.** A Wi-Fi PSK in a squashfs bundle is readable by
  anyone with the ISO. `network-preseed` says so on its page.
- Vulnerabilities in Slax, Linux Live Kit, or Debian/Slackware packages themselves — report those
  upstream. We do track the ones that affect what we build; see below.

## Known, documented, and not secret

[`docs/30-inventory/known-upstream-bugs.md`](docs/30-inventory/known-upstream-bugs.md) records
fourteen upstream issues found by analysis, several with security relevance. They are public
because a user of a 2023 image is better served by knowing than by not:

- **The shipped browser is three years old.** Both flavours carry chromium
  **117.0.5938.149** (September 2023); the `bookworm-security` suite the image already points at
  now offers **152.0.7977.82**. A live system is mostly used for browsing, so this is the largest
  attack surface in the image and the one with the shortest security half-life. The
  `chromium-current` recipe replaces it on Debian; on Slackware there is no supported route, and
  `remove-chromium` is the honest fallback.
- **The shipped busybox is from 2017** (`v1.26.2`), carrying CVE-2017-16544 — a terminal-escape RCE
  via `ash` tab completion, reachable because `/init` calls `debug_shell` six times — and
  CVE-2022-48174. The `initramfs-busybox` recipe replaces it with a current build.
- **Slackware Slax cannot verify any TLS certificate.** 144 CA certificates ship, but neither place
  OpenSSL looks for them exists, so every TLS client fails open on verification errors or simply
  fails. `fix-slackware-bugs` repairs it.
- **Slackware's `slackpkg` points at a mirror years ahead of the frozen base**, so updates install
  packages that do not run.

If you find something in this class that is *not* already documented there, it is worth reporting
privately first even though the fix will end up public.

## What we can and cannot verify

The development environment has no real hardware, no KVM, no Secure Boot. Claims about firmware,
signed boot chains, or physical media are ones we **cannot test here** — see the last section of
[CONTRIBUTING.md](CONTRIBUTING.md). A report from someone with real hardware is correspondingly
more valuable.
