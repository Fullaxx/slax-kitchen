#!/usr/bin/env python3
"""Read-only parsers for the formats a Slax ISO is made of.

Deliberately dependency-free and mount-free: everything here works by seeking around
byte offsets, so it runs unprivileged in a container with no loop devices, no FUSE and
no CAP_SYS_ADMIN.  That constraint is not incidental -- see docs/40-workflow/.

Covers:
  * ISO9660 volume descriptors (PVD / SVD-Joliet / boot record / terminator)
  * El Torito boot catalog, incl. the multi-section headers used for UEFI
  * Rock Ridge SUSP entries (RR / NM / PX / TF) in directory records
  * SYSLINUX boot-info-table patched into isolinux.bin
  * squashfs 4.0 superblocks, incl. the xz compressor-options block
  * FAT12 directories, for the EFI system partition boot.uefi builds
  * PE headers, as far as the certificate table that says a loader is signed

Nothing here writes.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field

SECTOR = 2048

SQUASHFS_COMP = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}
SQUASHFS_FLAGS = [
    (0x0001, "UNCOMPRESSED_INODES"), (0x0002, "UNCOMPRESSED_DATA"),
    (0x0004, "CHECK"),               (0x0008, "UNCOMPRESSED_FRAGMENTS"),
    (0x0010, "NO_FRAGMENTS"),        (0x0020, "ALWAYS_FRAGMENTS"),
    (0x0040, "DUPLICATES"),          (0x0080, "EXPORTABLE"),
    (0x0100, "UNCOMPRESSED_XATTRS"), (0x0200, "NO_XATTRS"),
    (0x0400, "COMPRESSOR_OPTIONS"),  (0x0800, "UNCOMPRESSED_IDS"),
]
ELTORITO_PLATFORM = {0x00: "x86-BIOS", 0x01: "PowerPC", 0x02: "Mac", 0xEF: "EFI"}


def _cstr(b: bytes) -> str:
    return b.decode("latin-1").strip().rstrip("\0").strip()


@dataclass
class BootEntry:
    kind: str                 # "validation" | "initial" | "section-header" | "section"
    platform: int | None = None
    bootable: bool = False
    media: str = ""
    load_segment: int = 0
    sector_count: int = 0
    lba: int = 0
    ident: str = ""

    @property
    def platform_name(self) -> str:
        return ELTORITO_PLATFORM.get(self.platform, f"0x{self.platform:02x}") \
            if self.platform is not None else ""


@dataclass
class Squashfs:
    path: str
    offset: int
    inodes: int
    mkfs_time: int
    block_size: int
    fragments: int
    compression: str
    block_log: int
    flags: int
    no_ids: int
    version: str
    bytes_used: int
    xz_dict_size: int | None = None
    xz_bcj: int | None = None

    @property
    def flag_names(self) -> list[str]:
        return [n for bit, n in SQUASHFS_FLAGS if self.flags & bit]


@dataclass
class IsoInfo:
    path: str
    size: int
    volume_id: str = ""
    system_id: str = ""
    application_id: str = ""
    publisher_id: str = ""
    preparer_id: str = ""
    volume_space: int = 0
    logical_block_size: int = 0
    joliet: bool = False
    rock_ridge: bool = False
    boot_catalog_lba: int | None = None
    boot_entries: list[BootEntry] = field(default_factory=list)
    isohybrid_mbr: bool = False
    gpt: bool = False
    partitions: list[dict] = field(default_factory=list)
    boot_info_table: dict | None = None

    @property
    def platforms(self) -> list[str]:
        return [e.platform_name for e in self.boot_entries
                if e.kind in ("initial", "section") and e.platform is not None]

    @property
    def uefi_bootable(self) -> bool:
        return any(e.platform == 0xEF for e in self.boot_entries
                   if e.kind in ("initial", "section"))


class IsoReader:
    """Minimal ISO9660 reader. Open, inspect, close."""

    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "rb")

    def close(self) -> None:
        self.f.close()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def _read(self, lba: int, count: int = 1) -> bytes:
        self.f.seek(lba * SECTOR)
        return self.f.read(count * SECTOR)

    # ---------------------------------------------------------------- volume --
    def info(self) -> IsoInfo:
        import os
        info = IsoInfo(path=self.path, size=os.path.getsize(self.path))

        # Bytes 0..512: isohybrid MBR, if any. A stock Slax ISO has all zeros here,
        # which is exactly why `dd` to a USB stick produces a non-bootable stick.
        self.f.seek(0)
        mbr = self.f.read(512)
        info.isohybrid_mbr = mbr[510:512] == b"\x55\xaa"
        if info.isohybrid_mbr:
            for i in range(4):
                e = mbr[446 + 16 * i: 446 + 16 * i + 16]
                if any(e):
                    lba, cnt = struct.unpack("<II", e[8:16])
                    info.partitions.append({
                        "bootable": e[0] == 0x80, "type": e[4],
                        "start_lba": lba, "sectors": cnt,
                    })
        self.f.seek(512)
        info.gpt = self.f.read(8) == b"EFI PART"

        for n in range(16, 32):
            vd = self._read(n)
            if vd[1:6] != b"CD001":
                break
            vtype = vd[0]
            if vtype == 1:                                    # Primary
                info.system_id = _cstr(vd[8:40])
                info.volume_id = _cstr(vd[40:72])
                info.volume_space = struct.unpack("<I", vd[80:84])[0]
                info.logical_block_size = struct.unpack("<H", vd[128:130])[0]
                info.publisher_id = _cstr(vd[318:446])
                info.preparer_id = _cstr(vd[446:574])
                info.application_id = _cstr(vd[574:702])
                self._root = (struct.unpack("<I", vd[158:162])[0],
                              struct.unpack("<I", vd[166:170])[0])
            elif vtype == 0:                                  # Boot record
                info.boot_catalog_lba = struct.unpack("<I", vd[71:75])[0]
            elif vtype == 2:                                  # Supplementary
                if vd[88:91] in (b"%/@", b"%/C", b"%/E"):
                    info.joliet = True
            elif vtype == 255:
                break

        if info.boot_catalog_lba is not None:
            info.boot_entries = self._boot_catalog(info.boot_catalog_lba)
        info.rock_ridge = self._has_rock_ridge()
        info.boot_info_table = self._boot_info_table(info)
        return info

    # ---------------------------------------------------------- el torito ----
    def _boot_catalog(self, lba: int) -> list[BootEntry]:
        cat = self._read(lba)
        out: list[BootEntry] = []
        i, expect_section = 0, 0
        while i < len(cat):
            ent = cat[i:i + 32]
            if not any(ent):
                break
            hdr = ent[0]
            if i == 0 and hdr == 0x01:
                out.append(BootEntry(kind="validation", platform=ent[1],
                                     ident=_cstr(ent[4:28])))
            elif hdr in (0x90, 0x91):
                cnt = struct.unpack("<H", ent[2:4])[0]
                out.append(BootEntry(kind="section-header", platform=ent[1],
                                     sector_count=cnt, ident=_cstr(ent[4:32])))
                expect_section = cnt
            elif hdr in (0x88, 0x00):
                seg, _sys, cnt = struct.unpack("<HBxH", ent[2:8])
                media = {0: "no-emulation", 1: "1.2M", 2: "1.44M",
                         3: "2.88M", 4: "hard-disk"}.get(ent[1] & 0x0F, str(ent[1]))
                kind = "section" if expect_section else "initial"
                plat = None
                if kind == "section":
                    expect_section -= 1
                    for prev in reversed(out):
                        if prev.kind == "section-header":
                            plat = prev.platform
                            break
                else:
                    plat = out[0].platform if out else 0x00
                out.append(BootEntry(kind=kind, platform=plat, bootable=(hdr == 0x88),
                                     media=media, load_segment=seg,
                                     sector_count=cnt,
                                     lba=struct.unpack("<I", ent[8:12])[0]))
            i += 32
        return out

    def _boot_info_table(self, info: IsoInfo) -> dict | None:
        """SYSLINUX boot-info-table, patched into isolinux.bin at build time.

        Because mkisofs/xorriso rewrites these 56 bytes *inside the output image*, the
        in-ISO isolinux.bin never matches the source file -- so never checksum it
        against bootfiles/isolinux.bin and expect a match.
        """
        entry = next((e for e in info.boot_entries if e.kind in ("initial", "section")
                      and e.platform == 0x00), None)
        if entry is None:
            return None
        blk = self._read(entry.lba)
        if b"isolinux" not in blk[:2048].lower():
            return None
        pvd_lba, file_lba, file_len, checksum = struct.unpack("<IIII", blk[8:24])
        return {"pvd_lba": pvd_lba, "file_lba": file_lba,
                "file_len": file_len, "checksum": checksum,
                "self_consistent": file_lba == entry.lba}

    # --------------------------------------------------------- rock ridge ----
    def _has_rock_ridge(self) -> bool:
        try:
            lba, length = self._root
        except AttributeError:
            return False
        data = self._read(lba, max(1, (length + SECTOR - 1) // SECTOR))
        return b"SP\x07\x01\xbe\xef" in data or b"RRIP_1991A" in data

    # ----------------------------------------------------------- squashfs ----
    def squashfs_images(self) -> list[Squashfs]:
        """Find every sector-aligned squashfs superblock in the image.

        Slax bundles live at /slax/modules/*.sb; scanning for the magic avoids having
        to walk the ISO9660 directory tree just to locate them.
        """
        self.f.seek(0)
        data = self.f.read()
        out, off = [], 0
        while True:
            i = data.find(b"hsqs", off)
            if i < 0:
                break
            off = i + 4
            if i % SECTOR:
                continue
            out.append(self._squashfs_at(data, i))
        return out

    @staticmethod
    def _squashfs_at(data: bytes, i: int) -> Squashfs:
        (_magic, inodes, mkfs_time, block_size, fragments, comp, block_log,
         flags, no_ids, s_major, s_minor) = struct.unpack("<IIIIIHHHHHH", data[i:i + 32])
        bytes_used = struct.unpack("<Q", data[i + 40:i + 48])[0]
        sq = Squashfs(path="", offset=i, inodes=inodes, mkfs_time=mkfs_time,
                      block_size=block_size, fragments=fragments,
                      compression=SQUASHFS_COMP.get(comp, str(comp)),
                      block_log=block_log, flags=flags, no_ids=no_ids,
                      version=f"{s_major}.{s_minor}", bytes_used=bytes_used)
        # Compressor options follow the 96-byte superblock as a metadata block.
        if flags & 0x0400 and sq.compression == "xz":
            hdr = struct.unpack("<H", data[i + 96:i + 98])[0]
            size = hdr & 0x7FFF
            if size >= 8:
                body = data[i + 98:i + 98 + size]
                sq.xz_dict_size = struct.unpack("<I", body[0:4])[0]
                sq.xz_bcj = struct.unpack("<I", body[4:8])[0]
        return sq


# -------------------------------------------------------------------- FAT12 --

@dataclass
class FatImage:
    label: str                                          # "" when it has none
    files: dict[str, bytes] = field(default_factory=dict)   # path -> its first bytes


def fat12(path: str, head: int = 4096) -> FatImage | None:
    """The label and every file of the FAT12 image at `path`, each file with its first
    `head` bytes, or None when it is not a FAT12 image this can read.

    For the EFI system partition: boot.uefi builds one with mkfs.vfat -F 12 and mtools, and
    reads an existing one back with this before it will replace it. Paths are the 8.3 short
    names joined with '/', as in EFI/BOOT/BOOTX64.EFI. The label is the root directory's
    label entry, which is what mlabel changes, or the boot sector's when there is none.
    FAT16 and FAT32 give None rather than half an answer: nothing here builds either.
    """
    def u16(b: bytes, o: int) -> int:
        return struct.unpack_from("<H", b, o)[0]

    try:
        with open(path, "rb") as f:
            boot = f.read(512)
            if len(boot) < 512 or boot[510:512] != b"\x55\xaa":
                return None
            bps, spc, reserved, nfats = u16(boot, 11), boot[13], u16(boot, 14), boot[16]
            root_entries, spf = u16(boot, 17), u16(boot, 22)
            total = u16(boot, 19) or struct.unpack_from("<I", boot, 32)[0]
            # FAT32 has no fixed root directory and no 16-bit FAT size: both zero.
            if (bps not in (512, 1024, 2048, 4096)
                    or spc not in (1, 2, 4, 8, 16, 32, 64, 128)
                    or not (reserved and nfats and root_entries and spf and total)):
                return None
            root_at = (reserved + nfats * spf) * bps
            data_at = root_at + -(-root_entries * 32 // bps) * bps
            csize = spc * bps
            clusters = (total * bps - data_at) // csize
            if not 0 < clusters < 4085:                 # the FAT12 limit, by FAT's own rule
                return None
            f.seek(0)
            img = f.read(total * bps)
    except OSError:
        return None
    if len(img) < total * bps:
        return None
    fat = img[reserved * bps:(reserved + spf) * bps]

    def read(c: int, n: int) -> bytes:
        """Up to n bytes of the cluster chain that starts at c."""
        out, seen = bytearray(), set()
        while 2 <= c < 0xFF7 and len(out) < n:
            if c in seen or c - 2 >= clusters:
                raise ValueError(f"cluster chain loops or runs off the image at {c}")
            seen.add(c)
            at = data_at + (c - 2) * csize
            out += img[at:at + csize]
            i = c + c // 2
            v = fat[i] | fat[i + 1] << 8
            c = v >> 4 if c & 1 else v & 0xFFF
        return bytes(out[:n])

    label, files = "", {}

    def walk(entries: bytes, prefix: str, depth: int) -> None:
        nonlocal label
        for i in range(0, len(entries) - 31, 32):
            e = entries[i:i + 32]
            if e[0] == 0x00:                            # the end of this directory
                return
            attr = e[11]
            if e[0] == 0xE5 or attr == 0x0F:            # deleted, or a long-name fragment
                continue
            if attr & 0x08:                             # the volume label
                if not prefix:
                    label = e[0:11].decode("latin-1").rstrip()
                continue
            name = e[0:8].decode("latin-1").rstrip()
            if name in (".", ".."):
                continue
            ext = e[8:11].decode("latin-1").rstrip()
            full = prefix + name + ("." + ext if ext else "")
            first, size = u16(e, 26), struct.unpack_from("<I", e, 28)[0]
            if attr & 0x10:
                if depth >= 16:
                    raise ValueError(f"{full}: directories nested too deep")
                walk(read(first, clusters * csize), full + "/", depth + 1)
            else:
                files[full] = read(first, min(size, head)) if size else b""

    try:
        walk(img[root_at:data_at], "", 0)
    except (ValueError, IndexError):
        return None
    if not label and boot[38] == 0x29:                  # the boot sector's own label field
        label = boot[43:54].decode("latin-1").rstrip()
    return FatImage(label="" if label == "NO NAME" else label, files=files)


def pe_signed(head: bytes) -> bool | None:
    """Whether the PE image that starts with `head` carries an Authenticode signature, or
    None when `head` is not the start of a PE image.

    A signature lives in the certificate table, data directory 4 of the optional header.
    grub-mkstandalone's output has none, and a loader signed after it was built (sbsign, or
    anything else that adds a signature) has one.
    """
    try:
        if head[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe:pe + 4] != b"PE\0\0":
            return None
        opt = pe + 24                                   # past the signature and COFF header
        magic = struct.unpack_from("<H", head, opt)[0]
        if magic not in (0x10B, 0x20B):                 # PE32, PE32+
            return None
        dirs = opt + (96 if magic == 0x10B else 112)
        if struct.unpack_from("<I", head, dirs - 4)[0] <= 4:    # NumberOfRvaAndSizes
            return False
        at, size = struct.unpack_from("<II", head, dirs + 4 * 8)
        return bool(at or size)
    except struct.error:
        return None


def boot_record(path: str) -> str:
    """What `kitchen unpack` records in origin.yaml of how the image boots, as flat YAML:
    boot_bios and boot_uefi for its El Torito entries, hybrid_mbr for an MBR in its first
    sector. These are written when an image is mastered, so they do not come with the tree,
    and `kitchen pack` warns about any a build will not write again (LAYERING.md, step 6).

    Empty for a file with no primary volume descriptor. info() reads one as an image with no
    boot entries and no MBR, which recorded would be a claim about an image that is not one.
    """
    with IsoReader(path) as r:
        info = r.info()
    if not info.volume_space:                 # set only from a primary volume descriptor
        return ""
    have = {"boot_bios": "x86-BIOS" in info.platforms, "boot_uefi": info.uefi_bootable,
            "hybrid_mbr": info.isohybrid_mbr}
    return "".join(f"{k}: {str(v).lower()}\n" for k, v in have.items())


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--boot":
        print(boot_record(argv[2]), end="")
        return 0
    if len(argv) < 2:
        print(f"usage: {argv[0]} [--boot] <file.iso>", file=sys.stderr)
        return 2
    with IsoReader(argv[1]) as r:
        info = r.info()
        print(f"{info.path}  ({info.size:,} bytes)")
        print(f"  volume id     {info.volume_id!r}   system id {info.system_id!r}"
              f"   application id {info.application_id!r}")
        print(f"  volume space  {info.volume_space:,} blocks x {info.logical_block_size}")
        print(f"  Rock Ridge    {info.rock_ridge}")
        print(f"  Joliet        {info.joliet}")
        print(f"  isohybrid MBR {info.isohybrid_mbr}    GPT {info.gpt}")
        print(f"  El Torito catalog @ LBA {info.boot_catalog_lba}")
        for e in info.boot_entries:
            if e.kind == "validation":
                print(f"    validation      platform={e.platform_name}")
            elif e.kind == "section-header":
                print(f"    section-header  platform={e.platform_name} entries={e.sector_count}")
            else:
                print(f"    {e.kind:<14}  platform={e.platform_name} bootable={e.bootable} "
                      f"media={e.media} sectors={e.sector_count} lba={e.lba}")
        print(f"  UEFI bootable {info.uefi_bootable}")
        if info.boot_info_table:
            b = info.boot_info_table
            print(f"  boot-info-table pvd_lba={b['pvd_lba']} file_lba={b['file_lba']} "
                  f"file_len={b['file_len']} checksum=0x{b['checksum']:08x} "
                  f"consistent={b['self_consistent']}")
        sqs = r.squashfs_images()
        print(f"  squashfs images: {len(sqs)}")
        for s in sqs:
            bcj = {1: "x86"}.get(s.xz_bcj, s.xz_bcj)
            print(f"    @0x{s.offset:09x} {s.bytes_used/1048576:8.1f} MiB  v{s.version} "
                  f"{s.compression} block={s.block_size} inodes={s.inodes} "
                  f"flags=0x{s.flags:04x} xz_dict={s.xz_dict_size} bcj={bcj}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
