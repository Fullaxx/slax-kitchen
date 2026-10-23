# Under `sudo`: the hand-back

**What it does.** Under `sudo`, every file a kitchen command writes belongs to root. As the
command ends, kitchen gives what it wrote in your work tree and output directory to
`SUDO_UID:SUDO_GID`. sudo sets those two to the user and group of whoever typed `sudo`. Without
`sudo`, none of this happens.

This page is the one place that says exactly what the hand-back does:
- which commands do it;
- which files it gives back and which it never touches;
- which ways of ending a command it survives;
- where it falls short.

The measurements on it were made on 2026-10-23 at `057b682`, unless a row says otherwise. They ran
as root with `SUDO_UID=1000 SUDO_GID=1000`, in a copy of the tree whose top directory uid 1000
owned, as a checkout does.

## Why it exists

**Some recipe steps need root.** `kitchen apply` checks before it starts, and refuses without it:
"no chroot capability, needed by … -- this container/host cannot run it". The steps are:

| verb | needs |
|---|---|
| `bundle.packages`, `bundle.script` | `chroot` and `mknod`: a real chroot of the image's bundles |
| `initramfs.files`, `initramfs.modules`, `initramfs.patch`, `initramfs.busybox` | `mknod`: the initramfs carries device nodes |

A recipe says so in `compat.privilege`, as `chroot` or `mknod`.
[What needs privilege](../40-workflow/container-vs-host.md) has the rest.

**`kitchen build` has no escalation of its own.** A profile with one of those recipes is built
with `sudo ./kitchen build`, so unpack, apply, pack and test all run as root. A project can instead
run only `kitchen apply` under `sudo`, as slax-wine's build script does.

**What breaks if the files stay root's.** Measured for
[#70](https://github.com/Fullaxx/slax-kitchen/issues/70), after one `sudo kitchen apply` in a tree
the user owned:
- three files were `root:root 0644`: the bundle, `.kitchen/journal.yaml` and
  `.kitchen/provenance.json`;
- the next step without `sudo` could not write them, or remove the directories root had made.

The steps that usually come next all run as you:
- **CI's boot step** writes logs and screenshots into `out/boot-tests/` as the runner user. With
  `out/` left `root:root 755`, it died on a permission error.
- **Publishing** creates files beside the image: `kitchen sources --markdown out/…`, and
  `sha256sum > out/SHA256SUMS` ([Publishing images](../40-workflow/publishing-images.md)).
- **Iterating** with `kitchen apply` of a `privilege: none` recipe, without `sudo`, has to rewrite
  the journal and the provenance record.
- **Cleaning up:** `rm -rf work/…` cannot empty a directory root made. slax-wine's build script
  recorded "one failed build wedged every subsequent one".

**What it replaced.** Before #70, three places chowned by hand:
- CI's boot job, after its build;
- slax-wine's build script, after every apply;
- this repository's own publishing page, which had a `sudo chown -R out/` step.

**Why the files are not simply left root's.** They are your build outputs in your own checkout, and
who owns them on disk never reaches the image (below). Leaving them root's works only if every
later step, deleting them included, also runs as root.

## When it runs, and to whom

| situation | what happens |
|---|---|
| root, run through `sudo` by a user | given to that user's `SUDO_UID:SUDO_GID`, for example `1000:1000` |
| a plain user, no `sudo` | nothing to do: the trap is never set, and the files are already yours |
| root without `sudo`, no `SUDO_UID` | nothing changes |
| `SUDO_UID=0`: `sudo` run by root, or a container that sets it | nothing changes: root is the user. Measured: the hand-back exits 0 and prints nothing |

`SUDO_GID` is the group sudo records for the user, normally their primary group. Both must be
numbers. `lib/handback.py`'s `invoker()` is the rule.

## Which commands

| command | gives back | what it registers |
|---|---|---|
| `kitchen fetch` | yes | only what it wrote: the ISO and its `.part` file, and the highest directory it created, with everything in it. Never the rest of `-o`'s directory, which can be `~/Downloads` |
| `kitchen unpack` | yes | its destination (`-o`, default `work`) |
| `kitchen apply` | yes | its work tree (`-w`, default `work`) |
| `kitchen pack` | yes | the image's directory, and the source tree (`-s`), because pack rebuilds `98-dpkg-db.sb` there |
| `kitchen build` | yes | `work/` and `out/` themselves, then what unpack, apply, pack and test register |
| `kitchen test` | yes | its evidence directory (`--out`, default `boot-tests/` beside the image) |
| `kitchen shell` | nothing of yours | it works in a temporary directory under `TMPDIR`, and removes it as it ends |
| `kitchen doctor`, `diff`, `probe`, `validate`, `status`, `selftest`, `boot-host`, `version`, `help` | nothing of yours | they read, or use temporary directories they remove as they end, or, for `boot-host`, the boot host's own disk |
| `kitchen sources --json/--markdown`, `fingerprint -o`, `upstream-diff --write`, `doctor --install-hooks` | **no** | they write and give nothing back. None needs root: run them as yourself. Measured: `sources --markdown`, `fingerprint -o` and `doctor --install-hooks` each left what it wrote `root:root`. `upstream-diff --write` is read from its code |

`kitchen test --record FILE`, the Tier C ledger, appends to `FILE`, which is not in the evidence
directory.
- An existing file keeps its owner.
- A file that `sudo` creates is root's, and so is a directory created for it.

Both were measured.

## Which files it gives back

**Every path that root owns, under what the command registered,** in a place that is yours. A
place is yours if:
- the command created it;
- you own it;
- it sits in a directory you own.

A path is registered before the command creates it, which is how a directory the command made
counts as yours.

What that came to on 2026-10-23 at `057b682`:

| run | given back |
|---|---|
| `kitchen unpack <iso> -o work/u` | 48 paths: `work/u`, its `.kitchen/origin.yaml`, and the unpacked image. But not `work/` itself: see [the first gap](#where-it-falls-short) |
| `kitchen apply rootcopy-overlay -w work/u` | 7: `.kitchen/journal.yaml`, `.kitchen/provenance.json`, and `iso/slax/rootcopy/` with the four paths in it |
| `kitchen apply` of a `bundle.script` recipe | 3: the new bundle, the recipe's rootcopy file, and a record file apply rewrote. The build chroot was removed |
| `kitchen pack -s work/u/iso -o out/u/u.iso` | 3: `out/u`, the image, and its `.provenance.json` |
| `kitchen test out/u/u.iso --structure` | 1: `out/u/boot-tests/` |
| `kitchen test out/u/u.iso --kernel`, on the boot host | 2: `u-kernel.serial.log` and `u-kernel.png` |
| `kitchen build minimal --keep`, where `work/` and `out/` were root's | 59, in three hand-backs (the preflight pass, apply, and the build's own), with `work/` and `out/` themselves among them |
| `kitchen fetch debian-64bit-12.2.0 -o dl/a/b --verify-only` | 3: `dl`, `dl/a` and `dl/a/b`, which `--verify-only` creates though it downloads nothing |

A download's ISO and `.part` file are given back by the code (`WROTE` in `lib/fetch.py`), not by a
measurement here. [#79](https://github.com/Fullaxx/slax-kitchen/issues/79) measured them left root's
before that code existed.

A hand-back that gave anything back says so on stderr: `gave 48 path(s) back to uid 1000, who ran
sudo`.

## Which files it never touches

| never | why |
|---|---|
| anything outside the registered paths | `-o` can name a directory full of other things, like `~/Downloads` |
| a place that is not yours | `sudo kitchen apply -w /etc` gives nothing away, and neither does a root-owned tree under `/srv`. #70's first draft took any path it was given, and would have handed over `/` after `-w /` |
| a file someone else owns | the command did not write it |
| a regular file with a second name | a hard link could be one of the host's files |
| a symlink's target | the link itself is changed, and never followed |
| another filesystem | a mount inside a work tree is not the work tree |

**It does change one thing besides the owner.** chown clears setuid and setgid bits, so the
hand-back puts them back.

**What tests this.** `test_what_root_wrote_under_sudo_is_given_back` covers every row but the
filesystem one. That row is read from `lib/handback.py`'s `_under()`, because a container that
cannot mount cannot set it up.

## Which ways of ending it survives

Measured on 2026-10-23 at `057b682`:
- `kitchen unpack` stands for the shell commands (unpack, pack, build, test);
- `kitchen apply` of a recipe with a `bundle.script` step stands for the Python ones (apply, fetch).

Each signal was sent to the command's process group, as Ctrl-C does: 0.2 s into unpack, and 10 s
into apply, while its chroot step ran.

| how it ends | shell command | Python command |
|---|---|---|
| success | exit 0, given back | exit 0, given back |
| a failure after it wrote something | exit 1, given back | exit 1, given back, chroot removed |
| Ctrl-C (INT) | exit 130, given back | ends by SIGINT, which a shell shows as 130; given back, chroot removed |
| TERM, e.g. `kill` | exit 143, given back | exit 143, given back, chroot removed |
| HUP, e.g. a dropped ssh session or a closed terminal | exit 129, given back | exit 129, given back, chroot removed |
| HUP under `nohup` | ignored: it carries on, ends 0, and gives back | the same |
| output piped to something that quits early, e.g. `\| head` | **killed by SIGPIPE, nothing given back** | exit 1, given back |
| QUIT (Ctrl-\\) | killed, nothing given back | killed, nothing given back, its build chroot left: 1.8 GiB for the recipe measured |
| KILL, e.g. `kill -9` or the OOM killer | killed, nothing given back | killed, nothing given back, its build chroot left: 1.8 GiB for the recipe measured |

**A closed terminal** gives everything back and ends 129. That is a real hangup, after which every
write to the terminal fails, not just a HUP.
- **Shell commands:** measured by closing the pty under `kitchen unpack`, three runs in three.
- **Python commands:** `test_a_hangup_from_a_closed_terminal_still_exits_129` closes one. Before
  `86d49d2` that case ended 120.

**The hand-back never changes why a command ended.** A path it cannot change is named on stderr as
`handback: <path>: <error>` and skipped, and the command's exit status is left as it was.

## Where it falls short

Measured on 2026-10-23, and reported, not yet fixed. Each row says what to do meanwhile.

1. **Parent directories `mkdir -p` creates stay root's.**
   - **Which commands:** `kitchen unpack -o work/u`, `kitchen pack -o out/u/u.iso` and
     `kitchen test --out t-out/a/b`. Each registers its destination before creating it, but not the
     parents it creates on the way.
   - **Measured:** after the three, `work/`, `out/`, `t-out/` and `t-out/a/` were root's.
   - **Not affected:** `kitchen fetch` registers the highest directory it creates, and
     `kitchen build` registers `work/` and `out/` themselves. A build gave both back.
   - **Meanwhile:** create the parent yourself first.
2. **A pipe closed early ends the shell commands with nothing given back.**
   - **Which commands:** unpack, pack, build and test write as they go. When their reader goes, for
     example `| head`, or `less` quit before the end, the next write kills them with SIGPIPE, which
     nothing traps. Measured with unpack.
   - **Not affected:** apply and fetch turn it into an error and give back. Measured with apply.
   - **Meanwhile:** send the output to a file, or through `tee`.
3. **A signal during a chroot step's cleanup leaves the rest of its chroot.**
   - **Where the chroot is:** `bundle.packages` and `bundle.script` build their chroot
     (`kitchen-pkg-*`, `kitchen-bs-*`) beside the work tree, not in it, and remove it once the
     bundle is built.
   - **Measured:** INT, TERM or HUP landing during that removal stopped it. Between 1.1 and
     1.7 GiB of the measured recipe's chroot stayed root's, 18,600 to 28,200 paths, outside the work
     tree apply gives back. That happened five times in five.
   - **Not affected:** a signal earlier in the step lets the removal finish.
   - **Meanwhile:** after an interrupted apply on `work/x`, `sudo rm -rf work/kitchen-*`.
4. **`kitchen shell` ended by TERM or HUP leaves its temporary tree.** The tree is 602 MiB for
   `01-core.sb`, root's, in `TMPDIR`. It is not in your tree.

## The image is never affected

Who owns the files on disk does not reach the image:
- **The image.** `kitchen pack` records every file in it as `root:root` (`-uid 0 -gid 0`, in
  `lib/pack.sh`).
  - Measured: a tree whose every path was uid 1000's packed to an image whose every entry reads
    `0 0`.
- **A bundle's contents are fixed when it is built.**
  - `bundle.fromDir`, `bundle.files` and `bundle.fromTarball` build with `-all-root`.
  - `bundle.packages` and `bundle.script` keep the owners that dpkg and the script set inside the
    chroot.
  - So giving `07-x.sb` back changes who owns that file, not what is inside it.

**Before #70 the image recorded the tree's owner.** An image packed from a uid-1000 tree booted with
`/etc` owned by uid 1000, which on Slax is `guest`
([anatomy of the container](../10-anatomy/iso-container.md)). Recording root came first, and it is
also what makes giving the tree back safe.

## Checking, recovering and testing

**Did it run?**
- The `gave N path(s) back` line on stderr says so, when it gave anything back.
- `find work out -user root` lists what root still owns there. That is what the rules above leave,
  or one of the gaps.

**After a `kill -9`, a QUIT, a power cut or one of the gaps above:**
- **Run the same command under `sudo` again.** It gives back everything root owns under what it
  registers, leftovers included.
  - `kitchen build` registers all of `work/` and `out/`, and gave back what earlier commands had
    left (2026-10-23, and in [#97](https://github.com/Fullaxx/slax-kitchen/issues/97)).
  - `kitchen apply` registers its whole work tree.
- **Or chown by hand:** `sudo chown -R "$(id -u):$(id -g)" work out`. Your shell expands `id` before
  `sudo` runs, so it names you.
- **Remove a leftover build chroot.** It sits beside the work tree, so for `-w work/x`:
  `sudo rm -rf work/kitchen-*`.

**Testing it without a second user.** Run as root with the two variables sudo would set:
`SUDO_UID=1000 SUDO_GID=1000 ./kitchen …`. That is how #83, #97 and this page were measured.

## History

| commit | ends how | what it fixed |
|---|---|---|
| `51c5699` ([#70](https://github.com/Fullaxx/slax-kitchen/issues/70)) | normally | the hand-back itself, for unpack, apply, pack, build and test, with Ctrl-C and failures; and pack recording every file as root's |
| `8584bd3` ([#79](https://github.com/Fullaxx/slax-kitchen/issues/79)) | normally | `sudo kitchen fetch` left `isos/` and the ISO root's |
| `2074336` ([#81](https://github.com/Fullaxx/slax-kitchen/issues/81)) | interrupted | TERM skipped the hand-back and left apply's 1.4 GiB build chroot |
| `789e0af` ([#83](https://github.com/Fullaxx/slax-kitchen/issues/83)) | interrupted | HUP did the same, and left apt running in the chroot |
| `86d49d2` | interrupted | after a closed terminal everything was given back, but the exit status read 120, not 129 |
| `057b682` ([#97](https://github.com/Fullaxx/slax-kitchen/issues/97)) | normally | a build that reached pack, where `work/` or `out/` already existed, gave back nothing at its end |

## Where it lives

- **`lib/handback.py`:**
  - `give_back()` is the walk and its rules;
  - `run()` wraps apply's and fetch's entry points, and makes TERM and HUP exits;
  - `main()` is the command line `kitchen`'s trap calls.
- **`kitchen`:**
  - `handback_add` registers a path, plain or `--new`;
  - `handback_exit` is the EXIT trap;
  - the INT, TERM and HUP traps turn those signals into exits.
- **The registrations:** `lib/unpack.sh`, `lib/pack.sh` and `lib/build.sh` (build and test), plus
  `GIVE_BACK` in `lib/apply.py` and `WROTE` and `MADE` in `lib/fetch.py`.
- **The tests:** `test_what_root_wrote_under_sudo_is_given_back`,
  `test_a_terminated_entry_point_still_gives_back`,
  `test_a_hangup_from_a_closed_terminal_still_exits_129` and
  `test_a_second_build_under_sudo_still_gives_back`, all in `tests/unit/test_apply.py`.
