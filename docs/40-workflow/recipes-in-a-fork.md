# Recipes in a fork

This project is meant to be forked. Two things a fork almost always wants — its own bundle
numbers, and its own recipes — and the decision rule between them:

> **Copy a recipe when you are changing what it *does*. Override a var when you are only
> changing a *value*.**

Copying costs you upstream fixes. `libreoffice.yaml` shipped with two wrong comments — a size
claim contradicted by its own measurements, and a dependency relationship stated backwards — and
both were corrected after it landed. A fork that had copied the file still has them.

A fork of a fork — one project building on another project's work, as slax-rpgs is to build on
slax-wine — starts from that project's released image instead. See
[LAYERING.md](../../LAYERING.md).

---

## Changing a value: override it in a profile

A **profile** is one complete build: the base ISO, an ordered recipe list, the output name, and
which tests to run. `kitchen build <profile>` runs unpack → apply → pack → test from it. It lives
in your git, so this is where a fork's own values belong — not in a shell alias and not in a
modified copy of someone else's recipe.

```yaml
apiVersion: slax-kitchen/v1
kind: Profile
metadata:
  name: myproject
base: {flavour: debian, arch: 64bit, version: "12.2.0"}
recipes:
  - uefi-bootable                          # bare name: the recipe's own vars
  - name: libreoffice
    vars: {bundle: 20-office}              # object form: override them
  - name: serial-console
    vars: {port: ttyS1, speed: "9600"}
```

Both forms may be mixed freely. Overrides **merge** over the recipe's defaults, so setting `port`
leaves `speed` at whatever the recipe says.

`name:` takes a path as readily as a name — `name: recipes/myproject/my-tools.yaml` — and the vars
reach the recipe either way. Name each recipe once: `my-tools` and
`recipes/myproject/my-tools.yaml` in one profile are the same recipe, and the profile is refused
rather than letting one entry's vars replace the other's.

Three things this will not let you do quietly:

| | |
|---|---|
| **A var the recipe does not declare** | error, naming the ones it does |
| **A value the schema rejects** | error at validation, before anything is built — `bundle: NONSENSE` fails the `NN-name` pattern rather than dying inside `bundle.packages` |
| **Forget what you used** | `kitchen status` prints the overrides, and they are recorded in `.kitchen/journal.yaml` |

`kitchen apply --profile <profile>` applies a profile's recipes to an existing work tree without
rebuilding the ISO. The tree has to be the base the profile declares — a mismatched `base.flavour`
or `base.arch` is refused before anything is touched, since a `when: arch==` step would otherwise
build the wrong half in silence.

**Only what a recipe exposes as a var can be overridden.** If you want to change something a recipe
hardcodes, either send us a patch making it a var — that is a good contribution — or copy the
recipe.

---

## Changing behaviour: keep your own recipes

Make a directory under `recipes/` and put them in it. That is the whole setup:

```
recipes/
  available/          this repo's library
  myproject/          yours
```

```sh
kitchen apply my-tools                    # resolves by bare name, no configuration
```

Every directory under `recipes/` is on the search path. What that gets you:

- **`40-schema` validates your recipes** like any other — you get the schema, the verb argument
  checks and the `NN-` bundle rule for free.
- **`90-doc-coverage` ignores them.** It polices only `recipes/available/`, so you do not owe the
  upstream cookbook a page for a recipe that is yours. Write docs for your own reasons.
- **The matrix runs on them**, so you get the same four-target coverage we do:

  ```sh
  ci/recipe-matrix.sh debian-64bit-12.2.0 isos/slax-64bit-debian-12.2.0.iso recipes/myproject
  ```

Two rules worth knowing:

- **`metadata.name` must match the filename stem.** `recipes/myproject/my-tools.yaml` needs
  `name: my-tools`. Recipes are referenced by name, so these have to agree.
- **A name that exists in two directories is an error**, naming both files. Nothing silently
  picks one. Rename yours, or name the file you mean by path — `kitchen apply` accepts a path
  anywhere, including outside the repo.

### Sidecar files

A recipe may have a directory of payload beside it — `enable-ssh.debian.files/`,
`bundle-from-dir.files/`. `src:` resolves relative to the recipe's own directory, so these travel
with the recipe wherever it lives. Copy both, or neither.

---

## Bundle numbers

Load order is the numeric prefix and **higher wins**, and **`10`–`89` is yours** — number freely
in there. `00`–`09` is the platform (upstream's `01`–`06`, plus the recipes here that adjust the OS
rather than add to it), `90`–`97` is headroom, and `98`/`99` are refused to recipes because a
collision there costs a saved session rather than an ordering.
The whole table, and the measurements behind the two refusals, are in
[which numbers are whose](../10-anatomy/bundles-squashfs.md#which-numbers-are-whose).

None of it is upstream's convention — upstream documents no ranges at all. It is ours, which is
why it is written down in one place.

Ties are not an error and not random: `sortmod` sorts on the number and falls back to an
alphabetical compare, so `07-branding` loads before `07-extras`. Stock Slax relies on this —
`01-core` before `01-firmware`. It is still worth avoiding, because nobody predicts it.

Every shipped recipe takes its bundle number from a var, so if one collides with yours, override it
in your profile rather than editing the recipe:

```yaml
  - name: add-packages
    vars: {bundle: 30-mytools}
```

The five application recipes — `chromium-current`, `firefox-esr`, `libreoffice`, `all-browsers`
and `debian-browsers` — sit at `10`–`14`, inside your range. That is deliberate: installing an application is downstream work, and they are
examples of it. Renumber them the same way if you want that space.

See [composing bundles](composing-bundles.md) for what a number actually decides.

---

## Sending something back

General-purpose recipes are welcome upstream; project-specific ones belong in your fork. The line
is roughly "would someone building an unrelated image want this?" — `firefox-esr` yes, "our
company's VPN certificates" no. [CONTRIBUTING.md](../../CONTRIBUTING.md) has the bar, and the
verification ladder you will be asked to state.
