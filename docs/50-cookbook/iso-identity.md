# `iso-identity` — label the image, and checksum it

**Status: boot-verified** — applied, packed, checksum verified with `sha256sum -c`, and booted.

```sh
kitchen apply iso-identity
kitchen pack
# -> out/slax-...-custom.iso
# -> out/slax-...-custom.iso.sha256
```

## Why bother

A customized image that still identifies itself as stock is a trap for whoever finds it later,
including you in six months. And upstream leaves almost all of these fields blank — measured on
every shipped image:

```
volume id        'slax'
system id        'LINUX'        ← genisoimage's own default
application id   'slax'
publisher        ''
data preparer    ''
volume set · copyright · abstract · bibliography    all empty
```

So there is nothing to overwrite, only fields to start filling in.

## What each field is

| | width | where it shows up |
|---|---|---|
| `volid` | 32 | **the one people see** — the filesystem label when the image is mounted or written to a stick |
| `appid` | 128 | `application id`, free text |
| `sysid` | 32 | `system id`; upstream leaves genisoimage's `LINUX` |
| `publisher` | 128 | who built it |
| `preparer` | 128 | what built it |

The verb **refuses an over-long value** rather than letting the mastering tool truncate it without a
word:

```
iso.metadata: volid is 40 characters; the ISO9660 Volume Identifier field holds 32.
Shorten it -- the mastering tool would truncate it silently.
```

## Variables

```yaml
vars:
  volid: SLAX-CUSTOM
  publisher: "built with slax-kitchen"
  appid: ""
  sysid: ""
  preparer: "slax-kitchen"
```

Override per build in a profile — a project names its own image without a recipe of its own:

```yaml
recipes:
  - name: iso-identity
    vars: {volid: MYPRODUCT, appid: "MYPRODUCT 1.0", preparer: "myproject"}
```

An empty value leaves that field as it is: whatever an earlier recipe set, or else what `kitchen
pack` writes by default, `slax` for the application id and `LINUX` for the system id. `appid`,
`sysid` and `preparer` became vars in [#68](https://github.com/Fullaxx/slax-kitchen/issues/68);
before, a profile setting them was refused and slax-wine wrote its own identity recipe. Verified:
that profile, applied and packed, reads back from the image's volume descriptor as volume id
`MYPRODUCT`, application id `MYPRODUCT 1.0` and preparer `myproject`.

## Changing `volid` does not break booting

Worth stating, because it looks like it should. `find_data` searches for a **directory** named
`slax` (`LIVEKITNAME`), not for a volume label — so relabelling the image is cosmetic as far as the
boot is concerned. Boot-verified with `volid: SLAX-CUSTOM`:

```
ok   serial contains 'Looking for slax data'
ok   serial contains 'Live Kit done, starting slax'
```

Renaming the **directory** is the thing that has consequences, and this recipe does not do that. See
[glossary → LIVEKITNAME](../00-overview/glossary.md).

## Checksums are written after mastering

Necessarily — the checksum of an image cannot live inside that image. `kitchen pack` writes it next
to the output once the ISO exists. From `kitchen build` on 2026-10-04, of the `example` profile with
`iso-identity` added; pack prints the image's path in full, shortened here:

```
ok   wrote out/slax-example-12.2.0.iso  (423 MiB)
note: xorriso uppercased the application id to SLAX
ok   wrote out/slax-example-12.2.0.iso.sha256
ok   wrote out/slax-example-12.2.0.iso.provenance.json
```

The filename inside is **relative**, so verification works from the output directory regardless of
where the image was built:

```sh
$ cd out && sha256sum -c slax-example-12.2.0.iso.sha256
slax-example-12.2.0.iso: OK
```

`algorithm: sha512` is also accepted. Anything else is refused — md5 and sha1 have no business
attesting a 400 MiB image in 2026.

## Signing

```yaml
- verb: iso.checksums
  algorithm: sha256
  sign: "your-key-id"
```

`kitchen pack` then runs `gpg --detach-sign --armor` on the checksum file. It signs the **checksum**,
not the ISO, which is the conventional shape: one small signed file covering a large one.

If the key is missing or gpg is not installed, the checksum is still written and a warning is
printed — signing failing should not cost you the build.

**No key material belongs in a recipe.** The recipe names a key id; the key lives in your keyring.

## Precedence

A flag on the command line beats the recipe, and says so:

```
$ kitchen pack --volid OVERRIDE
  hint volid=SLAX-CUSTOM overridden on the command line
```

That ordering matters for CI, where one recipe set is packed several ways. It holds for all five
fields. Until the publishing work tried to use them, `--sysid`, `--publisher` and `--preparer`
were listed by `kitchen help pack` and refused as unknown options, and a publisher hint beat the
flag.

`kitchen build` asserts the volume id the recipe asked for. It used to assert the stock `slax`, so a
profile with this recipe and `test: [structure]` packed `SLAX-CUSTOM` and then failed its own
structure test. `pack`, `kitchen build` and `ci/recipe-matrix.sh` now read the hints through one
function, `pack_hint` in `lib/hints.sh`.

## A note on the xorriso backend

xorriso **uppercases the application id**. `kitchen pack` warns when it happens. If you need the
metadata to come through exactly as written, use the default genisoimage backend — which is also
the faithful one. See [repack-iso](../40-workflow/repack-iso.md).
