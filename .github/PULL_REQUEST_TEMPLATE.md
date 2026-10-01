## What this changes

<!-- One or two sentences. If it fixes an issue, link it. -->

## Which rung did you actually reach?

Tick the highest one you **ran**, not the highest you believe holds. Claiming a rung you did not
reach is the one thing this project treats as a real error — everything downstream trusts these
words. The ladder is defined in
[CONTRIBUTING.md § 5](https://github.com/Fullaxx/slax-kitchen/blob/master/CONTRIBUTING.md#say-what-you-actually-verified).

- [ ] **schema-valid** — `./kitchen validate` passes
- [ ] **gate-clean** — `./kitchen selftest ci` passes
- [ ] **matrix-verified** — builds and passes structure assertions on all four targets
- [ ] **artifact boot-verified** — it booted, and `testkit` confirms the artifact reached the union
- [ ] **boot-verified** — booted to `slax login:` with all three livekit markers
- [ ] **runtime-verified** — the feature actually works on a booted desktop

Which targets did you build? <!-- e.g. debian-64bit only, or all four -->

If you skipped a target, does the recipe's `compat:` block say so?

## Verification, pasted

<!--
The commands and their output, copied. "Tests pass" is not evidence; the output is.
If you could not verify something, say which and why -- an honest gap is fine and a
wrong claim is not. We have no real hardware and no Secure Boot here either.
-->

```
```

## Checklist

- [ ] A recipe change has a matching page in `docs/50-cookbook/` (the `90-doc-coverage` gate requires it)
- [ ] An engine change has a unit test, or an explanation of why it cannot have one
- [ ] No gitignored working files, ISOs or bundles are in the diff
- [ ] Docs that state the old behaviour are updated — including docstrings

<!--
Every pull request gets the boot job: one direct-kernel boot that asserts the livekit
markers. Ask a maintainer to add the `boot-test` label if this change could affect
booting -- anything touching the initramfs, the bootloader, the kernel, or bundle load
order -- and the Tier C sweep runs too: the BIOS and UEFI menus, a USB image and
persistence.

The boot job runs once the build jobs it needs have finished; docs/60-testing/ci.md has
the job times. It boots under KVM: the job opens the runner's /dev/kvm, and boots under
TCG on a runner that has none. This note once said 45 minutes, which was the
`timeout-minutes:` ceiling in ci.yml read as if it were a runtime -- the kind of number
that makes people avoid asking for a test they should ask for.
-->
