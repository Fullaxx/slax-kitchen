# Notes for Claude

Pointers, not rules. Everything here lives somewhere else in the repo; this file only says
**when to go and read it**. A rule copied into two places is how the two drift — the argument
this project makes about regexes, counts and package databases applies to its own conventions.

## Before committing anything that answers an issue

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) § *"There is no pull request for maintainer-side fixes"*
and follow it exactly. It covers how the commit closes the issue and how many commits an issue
gets.

Not optional and not from memory: `6ecf019` fixed #17, #18 and #19 without it, and left three
issues open that the code had already answered.

## Before producing Tier C evidence

Read [`docs/60-testing/tier-c.md`](docs/60-testing/tier-c.md) § *"A recorded run needs a clean
tree"*. What the ledger records about the tree is decided when the ledger is written, not when the
run starts.

## Before adding or changing a check

Read the header comment of the gate or test you are touching, and `ci/lib.sh`'s opening. The
standing bar — *a check which cannot fail is worse than no check* — is stated there, and every
gate's comment records the specific failure that put it in the tree.
