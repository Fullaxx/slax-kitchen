# Notes for Claude

Pointers, not rules. Everything here lives somewhere else in the repo; this file only says
**when to go and read it**. A rule copied into two places is how the two drift — the argument
this project makes about regexes, counts and package databases applies to its own conventions.
The exceptions are about the session rather than the tree, and have no other copy to drift from.

## Before committing or pushing anything

Don't, until the user has inspected the work and asked. Stop where the commit would go, with the
work uncommitted, and hand it over: what changed, what you verified and how, and the commit
message you would use. Approving a plan is not approving its commits, asking for one commit is not
asking for the next, and a request to commit is not a request to push.

Sessions committed and pushed on their own while the framework was being built. That stage is
over: from 2026-09-19 the user inspects every change before it is committed.

## Before committing anything that answers an issue

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) § *"There is no pull request for maintainer-side fixes"*
and follow it exactly. It covers how the commit closes the issue and how many commits an issue
gets.

Not optional and not from memory: `6ecf019` fixed #17, #18 and #19 without it, and left three
issues open that the code had already answered.

## Before reviewing anything

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) § *"Proposing a fix is welcome, and it will be checked"* —
*a fix nobody re-derived is a claim, and this project does not run on claims*, which applies to a
review as much as to a proposed fix.

The one part of that with no home in the repo, because it is about this session and not about the
tree: **when the user says "self-review", that means you.** Read the diffs and the issues they
answer and report directly. Do not invoke the code-review skill — it is expensive, and being asked
to review is not being asked to pay for that.

## Before producing Tier C evidence

Read [`docs/60-testing/tier-c.md`](docs/60-testing/tier-c.md) § *"A recorded run needs a clean
tree"*. What the ledger records about the tree is decided when the ledger is written, not when the
run starts.

## Before adding or changing a check

Read the header comment of the gate or test you are touching, and `ci/lib.sh`'s opening. The
standing bar — *a check which cannot fail is worse than no check* — is stated there, and every
gate's comment records the specific failure that put it in the tree.
