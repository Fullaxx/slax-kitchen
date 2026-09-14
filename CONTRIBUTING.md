# Contributing

This project is meant to be forked. If you are building your own Slax image — a Wine box, a rescue
disk, a kiosk — the expected path is: fork, add your recipes, keep pulling from here.

That means most of this document is not about pull requests. It is about **diagnosing a problem
well enough to report it**, because failures in this domain are unusually quiet:

- an ISO that does not boot gives you a blank screen and nothing else
- a recipe that changed nothing reports success
- the same build behaves differently as `root` than as uid 1001 — that has broken our own CI twice
- a bundle can be built correctly and still be wrong, because it landed at the wrong number

"It doesn't boot" is not a report anybody can act on. The good news is that this repository has a
lot of machinery for turning that sentence into something specific, and the first half of this
document is a tour of it.

---

## 1. Engine or recipe?

Two different things live here, and nearly every routing decision follows from telling them apart.

| | |
|---|---|
| **The engine** | `kitchen`, `lib/`, `schema/`, `ci/`, the verbs. Generic. Knows nothing about Wine. |
| **Recipes** | `recipes/available/*.yaml`. Declarative. Say *what* to change, never *how*. |

A **verb** is engine. A **recipe** is a use of verbs. If your problem is "`bundle.files` wrote the
file to the wrong place", that is the engine. If it is "my Wine bundle is missing a library", that
is your recipe.

### Where to file

| Your problem | Goes to |
|---|---|
| A verb, the CLI, a gate, the schema, a doc page here | **this repository** |
| Your own recipe, your own project's packaging | **your fork's tracker** |
| Not sure | **your fork's tracker first** — the section below usually settles it, and a fork maintainer who has already triaged is far more useful to us than a raw report |

The useful skill is telling them apart, and the tools mostly answer it for you: `kitchen probe`
tells you whether the ISO is what you think it is, `kitchen diff` tells you what actually changed,
and `kitchen status` tells you what you actually applied. Two of the three usually end the argument.

**Upstream Slax is a third party.** Tomáš Matějíček's Slax and Linux Live Kit are not ours, and a
lot of surprising behaviour is theirs rather than ours. Check
[known upstream issues](docs/30-inventory/known-upstream-bugs.md) — there are thirteen documented,
several of which look exactly like a bug in this toolkit until you read them.

---

## Getting set up

```sh
git clone --recurse-submodules https://github.com/Fullaxx/slax-kitchen
cd slax-kitchen && ./kitchen doctor
```

**`--recurse-submodules` matters.** `vendor/linux-live` is upstream's source, pinned and vendored
unmodified; a lot of the documentation cites paths inside it, and `kitchen upstream-diff` needs it.
Without it the vendor gate skips rather than fails, so the omission is quiet — if you already
cloned, `git submodule update --init`.

`kitchen doctor` will tell you what your machine can and cannot do before you waste time on
something it cannot run. You do not need root, KVM, or a privileged container for most of this.

---

## 2. Five minutes of triage, before you write anything

Run these four, in this order. They are fast and they answer most questions outright.

```sh
./kitchen selftest ci                  # is the tree itself clean?   (~1 min, no ISO)
./kitchen doctor                       # what can this machine even do?
./kitchen status -v work               # what did I actually apply, and to what?
./kitchen probe out/my.iso             # is the output what I think it is?
```

**`selftest ci`** runs the same eleven gates CI runs. If it fails, fix that first — you are not
looking at the bug you think you are.

**`doctor`** is the single most useful thing to paste into a report. It reports every tool it needs,
every kernel capability it probed *by using it* rather than by parsing `CapEff`, and then the
derived answer: which tiers of work this machine can do at all.

**`status -v`** reads `<work>/.kitchen/` and prints where the tree came from (path + sha256), every
recipe applied **in order**, the verbs that actually ran, and the artifacts each produced. Recipes
are not idempotent and order matters, so "which recipes, in what order" is frequently the whole
answer.

**`probe`** matches an ISO against the four fingerprints in `compat/` and classifies every
difference as benign, explained by a known recipe, unexplained, or critical. Run it on your
**output**, not just your input.

---

## 3. Classify the failure

Five classes. The useful evidence differs sharply between them, so classify before you gather.

| Class | You see | Narrow it with |
|---|---|---|
| **A** Doesn't boot | blank screen, `boot:` prompt, panic, no login | `kitchen test --kernel` + the stage table below |
| **B** Built fine, changed nothing | the recipe reported success; the change is not there | `status` → `diff` → `testkit` |
| **C** Works here, not there | passes locally, fails in CI or on another machine | `doctor` on both |
| **D** One target only | fine on debian-64bit, broken on slackware-32bit | the recipe's `compat:` block |
| **E** Is this even ours? | smells like Slax itself | `probe`, then known upstream issues |

### Class A — "it doesn't boot" is six different bugs

Find the stage first. Everything else depends on it.

| Stage | Symptom | Usual cause |
|---|---|---|
| **1 · firmware** | nothing at all; "no bootable medium" | no El Torito entry; not isohybrid; UEFI with no ESP |
| **2 · bootloader** | `Failed to load COM32 file`, or a bare `boot:` prompt | mixed SYSLINUX generations — see [bootloader payloads](docs/10-anatomy/bootloader-payloads.md) |
| **3 · kernel** | stops after `Booting...`, or panics | wrong/missing `vmlinuz`; a kernel without `CONFIG_IA32_EMULATION` (the whole initramfs is i386) |
| **4 · initramfs** | `Could not locate slax data` | `find_data` timed out; the storage driver is not in the initramfs |
| **5 · union** | boots, but `slax activate` misbehaves | no aufs → **silent** downgrade to overlayfs |
| **6 · change_root** | livekit finishes, then no login | a broken bundle, or a `rootcopy`/`preinit` that exits |

Three tools make all six legible:

**Get a serial log.** Nothing else matters as much. Apply the `serial-console` recipe, or boot with
`console=ttyS0,115200n8`. Without it the interesting output goes to a screen nobody is capturing.

```sh
./kitchen test out/my.iso --kernel --seconds 300
```

`--kernel` boots the kernel and initramfs directly, bypassing the bootloader, and asserts three
markers: `Looking for slax data`, `Mounting bundles`, `Live Kit done, starting slax`. **Which
marker is missing tells you the stage.** The serial log and a screenshot land in a
`boot-tests/` directory beside the ISO.

The other modes prove less than they look: `--structure` parses the ISO and never boots anything;
`--bios` and `--uefi` capture a screenshot of the boot menu and are **evidence, not assertions**.

**Use `debug`.** Adding `debug` to the kernel command line drops you to a shell at six points inside
`/init`. It is the fastest way to answer "how far did it get".

> Boot parameters are matched as **substrings**, not flags. `nodebug` *enables* `debug`. Only
> `text` is word-anchored. This is [issue 12](docs/30-inventory/known-upstream-bugs.md), and it has
> caught us too.

**Use `testkit`.** Apply it alongside whatever you are testing and it prints the union type, the
mounted bundle list, the busybox version and the content of files you name — to the console, at
boot, before `change_root`:

```
### TESTKIT BEGIN
union: aufs
bundles: 01-core.sb 01-firmware.sb ... 08-ssh.sb
file /etc/systemd/system/multi-user.target.wants/ssh.service: symlink -> /lib/systemd/system/ssh.service
### TESTKIT END
```

`union: aufs` is the line to look at first. A kernel without aufs falls back to overlayfs silently,
and everything downstream of that gets strange.

### Class B — it built, and changed nothing

The most common real bug, and the most frustrating, because every step reported success.

```sh
./kitchen status -v work                  # did the step actually run, or was it `when:`-skipped?
./kitchen diff isos/stock.iso out/my.iso  # what changed between input and output?
./kitchen diff a.iso b.iso --bundles      # ...and inside a changed bundle
```

`status` will show you a step that was skipped by a `when:` guard, or a recipe you thought you
applied and did not. `diff` extracts nothing — it reads file extents straight out of both images
and compares content, modes and structure in about two seconds.

Then ask **"did my file land at a number that wins?"** Load order is the numeric prefix and
**higher wins**. A bundle numbered below something that ships the same path loses, silently, and
looks exactly like "my recipe did nothing". See [the union](docs/10-anatomy/union-and-persistence.md).

### Class C — works here, not there

Almost always privilege, uid, or a missing tool. `kitchen doctor` on both machines, and diff the
output. The section on snags at the end lists the ones that have already caught us.

### Class D — one target only

There are **four** targets: Debian and Slackware, each 32- and 64-bit. They differ more than you
would expect — different init, different package manager, different paths for the same
configuration, and in several places a feature that exists on one and cannot exist on the other.

Before reporting, run the recipe matrix on all four and say which ones fail:

```sh
./ci/recipe-matrix.sh debian-64bit-12.2.0 isos/slax-64bit-debian-12.2.0.iso
```

If it is *meant* to be one-flavour, that belongs in the recipe's `compat:` block, and the matrix
will report `skip` rather than `FAIL`.

### Class E — ours or upstream's?

```sh
./kitchen probe out/my.iso
```

A verdict of `MODIFIED (N unexplained differences)` with something you did not cause is worth
reporting. `DIFFERENT RELEASE` means the ISO is not the release it claims to be.

Then read [known upstream issues](docs/30-inventory/known-upstream-bugs.md). Thirteen documented,
including: the shipped busybox is from 2017; Slackware cannot verify **any** TLS certificate;
`nosound` is documented and not implemented; the ISO cannot boot on UEFI or be `dd`'d to a stick at
all without the recipes here.

---

## 4. Filing an issue

### Always include

```sh
./kitchen doctor --report      # version + commit, tool VERSIONS, capabilities, privilege tier
./kitchen status -v work       # what was applied, in order
./kitchen probe out/my.iso     # what the ISO actually is
```

The **issue forms ask for `doctor --report` and will not let you skip it**, because most
"works here, fails there" reports are answered by that block alone — a tool version, the uid you
ran as, or a missing kernel capability.

Plus:

- **which of the four targets**, exactly — `debian-64bit-12.2.0`, not "Debian"
- **the exact command you ran**, copied, not described
- **what you expected and what happened** — and if the thing you expected is in a doc, which doc

### Add, by class

| Class | Also include |
|---|---|
| A | the **serial log** from `kitchen test --kernel`, and which of the six stages you reached |
| B | the recipe YAML, and `kitchen diff` between input and output |
| C | `kitchen doctor` from **both** machines |
| D | the matrix result for all four targets |
| E | the `kitchen probe` verdict, in full |

### Not worth including

A photograph of a screen (get a serial log instead); "latest version" (paste the commit); a
description of the recipe (paste the recipe).

### If you cannot get that far

File it anyway. A vague report about a real problem beats silence — say what you tried and where
you got stuck. If the triage above did not work, **that is itself a bug in this document**, and
worth telling us about.

---

## 5. Pull requests

### The bar

```sh
./kitchen selftest ci        # all eleven gates, or it will not merge
```

The gates enforce, among other things: no binaries in the repository, no gitignored working files,
`vendor/linux-live` byte-identical to its pinned commit, shellcheck, schema validation, no
credentials, every internal link resolves, and **every recipe has a cookbook page and every
cookbook page has a recipe**.

CI re-runs all of them, so `--no-verify` only defers the failure.

### Say what you actually verified

This is the one thing the project treats as a real error. There is a ladder, and each rung means
something specific:

| Rung | Means | How |
|---|---|---|
| **schema-valid** | the YAML is well-formed, and every key is one a verb reads | `kitchen validate` |
| **gate-clean** | the tree passes the eleven gates | `kitchen selftest ci` |
| **matrix-verified** | it builds and passes structure assertions on all four targets | `ci/recipe-matrix.sh` |
| **artifact boot-verified** | it booted, and `testkit` confirmed the artifact reached the union | `testkit` + `kitchen test --kernel` |
| **boot-verified** | it booted to `slax login:` with all three livekit markers | `kitchen test --kernel` |
| **runtime-verified** | the feature actually works | a full desktop boot |

Every cookbook page opens with the rung it reached, and `ci/checks/95-status-vocab.sh` rejects
any word that is not on this list. It cannot tell whether the claim is *true* — only you can —
but it stops the vocabulary drifting back to a bare "verified", which is where it started and
which meant six different things across 23 pages.

**Matrix-verified is not boot-verified, and boot-verified is not runtime-verified.** A correct file
in the right place is not a working feature — `ssh.service` being symlinked is not sshd accepting a
login. Claim the rung you reached, not the one you hoped for. Several cookbook pages say "not
boot-tested" for exactly this reason, and that is a feature.

### Two labels have to exist

`.github/workflows/ci.yml` gates the boot job on a **`boot-test`** label, and `upstream-watch.yml`
files its issues under **`upstream-watch`**. Neither is defined in any configuration, so a fresh
fork has neither, and a workflow referencing a label that does not exist fails quietly. Create
them once:

```sh
gh label create boot-test      --description "Run the QEMU boot job on this PR" --color 0e8a16
gh label create upstream-watch --description "Filed by the weekly upstream drift check" --color fbca04
```

### Boot tests on a PR

The boot job does **not** run on pull requests by default — GitHub runners have no KVM, so it is
slow. Add the **`boot-test` label** to your PR to opt in. This is documented nowhere else, which is
our fault.

### Recipes: what we will take

Upstream a recipe here if it is **generally useful to Slax users** — `enable-ssh`, `boot-cmdline`,
`locale-timezone-keyboard`. Keep it in your fork if it only makes sense for your project — a
`wine-runtime` recipe belongs in `slax-wine`, not here.

Rough test: would somebody who has never heard of your project want it?

A recipe we can take needs:

- a **cookbook page** in `docs/50-cookbook/` — the gate enforces this, and the page should say what
  it changes, what it costs, and what it does *not* do
- an honest **`compat:` block** — flavours, arches, and `privilege:` (`none`, `mknod`, `chroot`,
  `kvm`), which is what lets `doctor` tell a user whether their machine can run it
- **matrix-verified on all four targets**, or a `compat:` block that explains the skip
- comments that say **why**, and record what you measured

That last one is the strongest convention in the repository. Read any recipe in
`recipes/available/`: the comments explain trade-offs, name the failure they are avoiding, and cite
measurements. A recipe whose comments only restate the YAML will be sent back.

### Engine changes

A new verb needs: the implementation, the `schema/recipe.schema.json` enum entry, a
`VERB_REQUIRES` entry if it needs tools or capabilities, a section in
[the verb reference](docs/90-reference/verbs.md), and a recipe that exercises it.

Pure logic — anything that does not need an ISO — belongs in `tests/unit/test_apply.py`, which runs
in milliseconds as one of the eleven gates. Every case in that file is a bug that actually shipped.

**A check that cannot fail is worse than no check.** This has bitten this project at least four
times — a workflow that never opened an issue because `$?` after a pipeline is `tee`'s status; a
fidelity test that compared a tree against itself; a boot test whose only assertion was "a
screenshot exists"; a verb that reported "cmdline updated on 2 entries" while changing nothing. If
you add a test, **make it fail on purpose once** and check it says something useful.

---

## 6. Writing a recipe: the parts that surprise people

**Load order is the numeric prefix, and higher wins.** `01`–`06` are upstream's. `99` is
`savechanges`. Yours goes in `07`–`98`. Override, do not edit: a 4 KiB bundle at `07` beats a
122 MiB one at `01`, and leaves the original byte-identical so `probe` still recognises it.

**Debian's package database is cumulative, and this is a trap.** Every Debian bundle carries its
own `var/lib/dpkg/status`, growing 295 → 600 across the stack. Because higher wins, a new bundle
that ships a *smaller* status shadows the real one and dpkg forgets most of the system. If your
recipe installs packages, stack the whole thing in `from:`, not just `01-core`. See
[the bundle map](docs/30-inventory/bundle-map.md).

> **Our own `add-packages` recipe currently gets this wrong**, and since its page calls it "the
> template recipe", anyone copying it inherits the bug. Measured: with the shipped
> `from: [01-core]`, the resulting `07-extras.sb` ships a `var/lib/dpkg/status` listing **299**
> packages and sits above `05-chromium`'s **600** — so on a fully loaded image dpkg loses about
> three hundred packages. `firmware-refresh` and `users-and-auth` have the same shape. A fix is
> queued; until then, name the full stack in `from:` yourself. Found while writing this document,
> which is roughly the point of it.

**`bundle.files` builds a bundle in one shot** and refuses to write one that already exists, so
several steps cannot accumulate into the same bundle. Split by flavour with `when:`, not by concern.

**Paths cannot escape their tree.** Every `dest:` is resolved inside the tree the verb owns, with
symlinks resolved first; `..` is refused.

**Recipes are not idempotent.** Applying one twice is a mistake, and `apply` now says so.

---

## 7. Snags we already hit

Every one of these is measured, and every one cost somebody a session.

| | |
|---|---|
| **uid matters** | Running as root hides failures that appear at uid 1001. Two CI failures here, both "worked locally". |
| **`proot` is unsafe** | 5.1.0 does not translate `statx()`, so `stat` reads the **host** filesystem from inside the fake root. Silent and selective. Use real `chroot`. |
| **initramfs work needs `CAP_MKNOD`** | The archive holds seven device nodes; a `cpio` without it turns them into empty files and the image cannot open its own console. |
| **`xz` must support `--check=crc32`** | The kernel's decoder cannot do CRC64, which is xz's default. |
| **Do not unpack with `7z`** | It drops Rock Ridge modes. Fine for reading. |
| **No `/dev/kvm` is a speed problem** | Not a capability one. TCG is 10–20× slower; everything still works. |
| **busybox dispatches on `argv[0]`** | Invoked by any other name it answers `--list` with one line — which reads exactly like "this binary will not run here". |
| **readdir order varies by filesystem** | Comparing ISO *sector positions* tests your build host, not your build. |
| **Bundle numbers collide** | `add-packages` uses `07`, `network-preseed` `08`, `firmware-refresh` `09`. Pick your own. |

---

## 8. What we cannot test, and why your report matters

The development container has no KVM, no loop devices, no user namespaces and no real hardware. So
a large part of this project is verified by structure assertions and direct-kernel boots, and the
following are **not** routinely exercised by anybody here:

- real hardware of any kind — GPUs, wireless, laptops that suspend
- writing to a physical USB stick and booting a real machine from it
- Secure Boot and MOK enrolment
- persistence across reboots on real media
- the full desktop, as opposed to reaching a login prompt

If you are running this on real hardware, you are testing something we cannot. A report from you
about a machine that will not boot is more valuable than it feels — it is a class of evidence this
project structurally cannot generate for itself.

---

## Security

Please do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Licence

MIT for this repository's own code, docs and recipes. Slax, Linux Live Kit and everything inside a
built ISO carry their own licences — [NOTICE.md](NOTICE.md) sets out the boundary and the
obligations that come with redistributing an image. By contributing you agree your contribution is
licensed under the same terms.
