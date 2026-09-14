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
wrong claim is not. We have no real hardware, no KVM and no Secure Boot here either.
-->

```
```

## Checklist

- [ ] A recipe change has a matching page in `docs/50-cookbook/` (the `90-doc-coverage` gate requires it)
- [ ] An engine change has a unit test, or an explanation of why it cannot have one
- [ ] No gitignored working files, ISOs or bundles are in the diff
- [ ] Docs that state the old behaviour are updated — including docstrings

<!--
Boot tests do not run on pull requests by default, because they take 45 minutes under
TCG. Ask a maintainer to add the `boot-test` label if this change could affect booting
-- anything touching the initramfs, the bootloader, the kernel, or bundle load order.
-->
