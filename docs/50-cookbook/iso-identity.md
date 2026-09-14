# `iso-identity` — label the image, and checksum it

**Status: verified** — applied, packed, checksum verified with `sha256sum -c`, and boot-verified.

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
to the output once the ISO exists:

```
ok   wrote out/slax-example-12.2.0.iso  (415 MiB)
ok   wrote out/slax-example-12.2.0.iso.sha256
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

That ordering matters for CI, where one recipe set is packed several ways.

## A note on the xorriso backend

xorriso **uppercases the application id**. `kitchen pack` warns when it happens. If you need the
metadata to come through exactly as written, use the default genisoimage backend — which is also
the faithful one. See [repack-iso](../40-workflow/repack-iso.md).
