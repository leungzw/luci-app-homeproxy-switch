#!/usr/bin/env python3
"""Build apk packages for OpenWrt 25.x (apk-tools 3) without needing any toolchain.

Two container flavours are supported:

  v3 / ADB   The format OpenWrt 25.12 itself publishes. The file is

                 "ADBd" + raw-deflate( container )
                 container = file_header("ADB." + schema) + ADB block + DATA block per file

             Same layout `apk mkpkg` produces, see apk-tools src/{app_mkpkg.c,adb.c}.
             This is what OpenWrt 25 users install.

  v2         The historical format Alpine apk used (still readable by apk-tools 3
             via src/extract_v2.c): gzip(tar(.PKGINFO)) followed by gzip(tar(files)).
             Fedora/Alpine/older tools accept nothing else, so it ships as a fallback.

    python3 tools/build_apk.py                 # v3 (ADB) — the OpenWrt 25 target
    python3 tools/build_apk.py -v 1.1.0 -r 1
    python3 tools/build_apk.py -t v3
    python3 tools/build_apk.py -t v2           # legacy Alpine format (experimental)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import struct
import sys
import tarfile
import zlib
from pathlib import Path

PKG_NAME = "luci-app-homeproxy-switch"
VERSION = "1.0.0"
RELEASE = "1"

# --- adb primitive types (src/adb.h) ----------------------------------------
ADB_TYPE_SPECIAL = 0x00000000
ADB_TYPE_INT = 0x10000000
ADB_TYPE_INT_32 = 0x20000000
ADB_TYPE_INT_64 = 0x30000000
ADB_TYPE_BLOB_8 = 0x80000000
ADB_TYPE_BLOB_16 = 0x90000000
ADB_TYPE_BLOB_32 = 0xA0000000
ADB_TYPE_ARRAY = 0xD0000000
ADB_TYPE_OBJECT = 0xE0000000
ADB_NULL = 0x00000000

# --- blocks & file header ----------------------------------------------------
ADB_BLOCK_ADB = 0
ADB_BLOCK_DATA = 2
ADB_BLOCK_EXT = 3
ADB_BLOCK_ALIGNMENT = 8
ADB_FORMAT_MAGIC = 0x2E424441  # "ADB."
ADB_SCHEMA_PACKAGE = 0x676B6370  # "pckg"

# --- field indices (src/apk_adb.h) ------------------------------------------
ADBI_PI_NAME = 1
ADBI_PI_VERSION = 2
ADBI_PI_HASHES = 3
ADBI_PI_DESCRIPTION = 4
ADBI_PI_ARCH = 5
ADBI_PI_LICENSE = 6
ADBI_PI_ORIGIN = 7
ADBI_PI_MAINTAINER = 8
ADBI_PI_URL = 9
ADBI_PI_INSTALLED_SIZE = 0x0C
ADBI_PI_DEPENDS = 0x0F

ADBI_PKG_PKGINFO = 1
ADBI_PKG_PATHS = 2
ADBI_PKG_SCRIPTS = 3

ADBI_DI_NAME = 1
ADBI_DI_ACL = 2
ADBI_DI_FILES = 3

ADBI_FI_NAME = 1
ADBI_FI_ACL = 2
ADBI_FI_SIZE = 3
ADBI_FI_MTIME = 4
ADBI_FI_HASHES = 5
ADBI_FI_TARGET = 6

ADBI_ACL_MODE = 1
ADBI_ACL_USER = 2
ADBI_ACL_GROUP = 3

ADBI_SCRPT_POSTINST = 3
ADBI_SCRPT_POSTDEINST = 5
ADBI_SCRPT_POSTUPGRADE = 7

UID_LEN = 20  # apk_digest_alg_len(APK_DIGEST_SHA1)

ARCHIVE_MODE = 0o644
SCRIPT_MODE = 0o755
DIR_MODE = 0o755


class Obj:
    """In-progress adb object: slots indexed 1..N like the C counterpart."""

    def __init__(self, db: "Adb", num_fields: int):
        self.db = db
        self.slots = [ADB_NULL] * (num_fields + 1)

    def __setitem__(self, field: int, val: int) -> None:
        self.slots[field] = val


class Adb:
    """Minimal re-implementation of the adb writer (src/adb.c)."""

    def __init__(self) -> None:
        # struct adb_hdr { u8 compat_ver; u8 ver; u16 reserved; u32 root; }
        self.buf = bytearray(8)
        self.root = ADB_NULL

    # --- data area ---------------------------------------------------------
    def _reserve(self, data: bytes, align: int) -> int:
        if align > 1:
            pad = (-len(self.buf)) % align
            if pad:
                self.buf.extend(b"\x00" * pad)
        off = len(self.buf)
        self.buf.extend(data)
        return off

    # --- primitives --------------------------------------------------------
    def blob(self, data: bytes) -> int:
        if not data:
            return ADB_NULL
        n = len(data)
        if n <= 0xFF:
            return ADB_TYPE_BLOB_8 | self._reserve(bytes([n]) + data, 1)
        if n <= 0xFFFF:
            return ADB_TYPE_BLOB_16 | self._reserve(struct.pack("<H", n) + data, 2)
        return ADB_TYPE_BLOB_32 | self._reserve(struct.pack("<I", n) + data, 4)

    def string(self, value: str) -> int:
        return self.blob(value.encode())

    def integer(self, value: int) -> int:
        if value >= 0x100000000:
            return ADB_TYPE_INT_64 | self._reserve(struct.pack("<Q", value), 4)
        if value >= 0x10000000:
            return ADB_TYPE_INT_32 | self._reserve(struct.pack("<I", value), 4)
        return ADB_TYPE_INT | value

    def finalize(self, obj: Obj, vtype: int) -> int:
        """Mirror __adb_w_obj(): slot 0 holds n = highest used index + 1."""
        highest = max([i for i, v in enumerate(obj.slots) if v != ADB_NULL], default=0)
        if highest == 0:
            return ADB_NULL
        n = highest + 1
        slots = [n] + obj.slots[1:n]
        payload = b"".join(struct.pack("<I", v) for v in slots)
        return vtype | self._reserve(payload, 4)

    def finalize_object(self, obj: Obj) -> int:
        return self.finalize(obj, ADB_TYPE_OBJECT)

    def finalize_array(self, obj: Obj) -> int:
        return self.finalize(obj, ADB_TYPE_ARRAY)

    def payload(self) -> bytes:
        out = bytearray(self.buf)
        out[4:8] = struct.pack("<I", self.root)
        return bytes(out)


# ---------------------------------------------------------------------------
# file tree helpers
# ---------------------------------------------------------------------------
class FileEntry:
    __slots__ = ("name", "arcname", "data", "mode", "mtime")

    def __init__(self, arcname: str, data: bytes, mode: int, mtime: int):
        self.arcname = arcname
        self.data = data
        self.mode = mode
        self.mtime = mtime


def collect_tree(files_dir: Path, mtime: int):
    """Return (ordered dirs, {dirname: [FileEntry]}); dirs include "" for the root."""
    entries: dict[str, list[FileEntry]] = {}
    dirs = set([""])

    for path in sorted(files_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(files_dir))
        parent = os.path.dirname(rel)
        while parent:
            dirs.add(parent)
            parent = os.path.dirname(parent)
        is_script = rel.startswith("usr/bin/")
        entries.setdefault(os.path.dirname(rel), []).append(
            FileEntry(rel, path.read_bytes(),
                      SCRIPT_MODE if is_script else ARCHIVE_MODE, mtime)
        )

    ordered_dirs = sorted(dirs)
    for files in entries.values():
        files.sort(key=lambda f: f.arcname)
    return ordered_dirs, entries


# ---------------------------------------------------------------------------
# APK v3 (ADB container)
# ---------------------------------------------------------------------------
def build_v3(files_dir: Path, pkgver: str, meta: dict, mtime: int) -> bytes:
    db = Adb()

    # Unique id slot: reserve now (zeros), patch after the whole ADB block exists.
    uid_payload_off = db._reserve(bytes([UID_LEN]) + b"\x00" * UID_LEN, 1) + 1
    uid_val = ADB_TYPE_BLOB_8 | (uid_payload_off - 1)

    ordered_dirs, entries = collect_tree(files_dir, mtime)
    installed_size = 0

    def acl_obj(mode: int) -> int:
        acl = Obj(db, 3)
        acl[ADBI_ACL_MODE] = db.integer(mode)
        acl[ADBI_ACL_USER] = db.string("root")
        acl[ADBI_ACL_GROUP] = db.string("root")
        return db.finalize_object(acl)

    paths = Obj(db, len(ordered_dirs))
    for idx, dirname in enumerate(ordered_dirs, start=1):
        files = Obj(db, len(entries.get(dirname, [])))
        for fidx, f in enumerate(entries.get(dirname, []), start=1):
            file_obj = Obj(db, 6)
            file_obj[ADBI_FI_NAME] = db.string(Path(f.arcname).name)
            file_obj[ADBI_FI_HASHES] = db.blob(hashlib.sha256(f.data).digest())
            file_obj[ADBI_FI_SIZE] = db.integer(len(f.data))
            file_obj[ADBI_FI_MTIME] = db.integer(f.mtime)
            file_obj[ADBI_FI_ACL] = acl_obj(f.mode)
            files[fidx] = db.finalize_object(file_obj)
            installed_size += len(f.data)

        dir_obj = Obj(db, 3)
        dir_obj[ADBI_DI_NAME] = db.string(dirname)
        dir_obj[ADBI_DI_ACL] = acl_obj(DIR_MODE)
        dir_obj[ADBI_DI_FILES] = db.finalize_array(files)
        paths[idx] = db.finalize_object(dir_obj)

    # Package metadata
    info = Obj(db, 0x15)
    info[ADBI_PI_NAME] = db.string(PKG_NAME)
    info[ADBI_PI_VERSION] = db.string(pkgver)
    info[ADBI_PI_HASHES] = uid_val
    info[ADBI_PI_DESCRIPTION] = db.string(meta["description"])
    info[ADBI_PI_ARCH] = db.string("noarch")
    info[ADBI_PI_LICENSE] = db.string(meta["license"])
    info[ADBI_PI_ORIGIN] = db.string(PKG_NAME)
    info[ADBI_PI_MAINTAINER] = db.string(meta["maintainer"])
    info[ADBI_PI_URL] = db.string(meta["url"])
    info[ADBI_PI_INSTALLED_SIZE] = db.integer(max(1, installed_size))
    if meta.get("depends"):
        deps = Obj(db, len(meta["depends"]))
        for i, dep in enumerate(meta["depends"], start=1):
            deps[i] = db.string(dep)
        info[ADBI_PI_DEPENDS] = db.finalize_array(deps)

    scripts = Obj(db, 7)
    scripts[ADBI_SCRPT_POSTINST] = db.blob(meta["postinst"].encode())
    scripts[ADBI_SCRPT_POSTDEINST] = db.blob(meta["postdeinst"].encode())
    scripts[ADBI_SCRPT_POSTUPGRADE] = db.blob(meta["postinst"].encode())

    pkg = Obj(db, 5)
    pkg[ADBI_PKG_PKGINFO] = db.finalize_object(info)
    pkg[ADBI_PKG_PATHS] = db.finalize_array(paths)
    pkg[ADBI_PKG_SCRIPTS] = db.finalize_object(scripts)
    db.root = db.finalize_object(pkg)

    payload = bytearray(db.payload())
    digest = hashlib.sha256(bytes(payload)).digest()  # computed with the id zeroed
    payload[uid_payload_off:uid_payload_off + UID_LEN] = digest[:UID_LEN]

    container = struct.pack("<II", ADB_FORMAT_MAGIC, ADB_SCHEMA_PACKAGE)
    container += adb_block(ADB_BLOCK_ADB, bytes(payload))

    for pidx, dirname in enumerate(ordered_dirs, start=1):
        for fidx, f in enumerate(entries.get(dirname, []), start=1):
            if not f.data:
                continue
            hdr = struct.pack("<II", pidx, fidx)  # struct adb_data_package
            container += adb_block(ADB_BLOCK_DATA, hdr + f.data)

    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return b"ADBd" + compressor.compress(container) + compressor.flush()


def adb_block(btype: int, payload: bytes) -> bytes:
    rawsize = len(payload) + struct.calcsize("<I")
    if rawsize <= 0x3FFFFFFF:
        header = struct.pack("<I", (btype << 30) + rawsize)
    else:
        header = struct.pack("<IQ", (ADB_BLOCK_EXT << 30) + btype, rawsize)
    pad = (-rawsize) % ADB_BLOCK_ALIGNMENT
    return header + payload + b"\x00" * pad


# ---------------------------------------------------------------------------
# APK v2 (legacy gzip concatenation)
# ---------------------------------------------------------------------------
def build_v2(files_dir: Path, pkgver: str, meta: dict, mtime: int) -> bytes:
    ordered_dirs, entries = collect_tree(files_dir, mtime)
    flat = [(dirname, f) for dirname in ordered_dirs for f in entries.get(dirname, [])]
    installed_size = max(1, sum(len(f.data) for _, f in flat))

    data_tgz = tar_bytes(
        [(dirname, f) for dirname in ordered_dirs for f in entries.get(dirname, [])],
        ordered_dirs, mtime)

    data_gz = gzip_bytes(data_tgz)

    pkginfo = "\n".join([
        f"# Generated by {PKG_NAME} build_apk.py",
        f"pkgname = {PKG_NAME}",
        f"pkgver = {pkgver}",
        f"pkgdesc = {meta['description']}",
        f"url = {meta['url']}",
        f"license = {meta['license']}",
        f"maintainer = {meta['maintainer']}",
        f"size = {installed_size}",
        "arch = noarch",
        f"origin = {PKG_NAME}",
    ] + [f"depend = {d}" for d in meta.get("depends", [])] + [
        # apk hashes the *compressed* data stream, not the tar: the check in
        # src/extract_v2.c runs over the bytes it read off the stream.
        f"datahash = {hashlib.sha256(data_gz).hexdigest()}",
        "",
    ]).encode()

    # Exact names matter: extract_v2.c does strcmp(fi->name, ".PKGINFO") and
    # looks up scripts by apk_script_types[] against name[1:].
    control = tar_archive([
        (".PKGINFO", pkginfo, ARCHIVE_MODE),
        (".post-install", meta["postinst"].encode(), SCRIPT_MODE),
        (".post-deinstall", meta["postdeinst"].encode(), SCRIPT_MODE),
        (".post-upgrade", meta["postinst"].encode(), SCRIPT_MODE),
    ])
    return gzip_bytes(control) + data_gz


def tar_archive(members) -> bytes:
    """One single tar stream; apk's tar reader stops at the first EOF block,
    so concatenated tars inside a gzip member would be silently dropped."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = mode
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def tar_bytes(items, ordered_dirs, mtime: int) -> bytes:
    """gzip-able tar of the payload; directory entries come first, like opkg needs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for dirname in ordered_dirs:
            dirname = dirname.lstrip("./")
            if not dirname:
                continue
            info = tarfile.TarInfo(dirname)
            info.type = tarfile.DIRTYPE
            info.mode = DIR_MODE
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tf.addfile(info)
        for _, f in items:
            info = tarfile.TarInfo(f.arcname)
            info.size = len(f.data)
            info.mode = f.mode
            info.mtime = f.mtime
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tf.addfile(info, io.BytesIO(f.data))
    return buf.getvalue()


def gzip_bytes(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
def read_make_var(makefile: Path, var: str, default: str) -> str:
    if not makefile.is_file():
        return default
    for line in makefile.read_text().splitlines():
        line = line.strip()
        if line.startswith(var + ":="):
            return line.split(":=", 1)[1].strip()
        if line.startswith(var + "="):
            return line.split("=", 1)[1].strip()
    return default


POSTINST = """#!/bin/sh
# Refresh the LuCI menu caches and let rpcd pick up the new ACL.
rm -f /tmp/luci-indexcache* /tmp/luci-modulecache*
[ -x /etc/init.d/rpcd ] && /etc/init.d/rpcd restart
exit 0
"""

POSTDEINST = """#!/bin/sh
rm -f /tmp/luci-indexcache* /tmp/luci-modulecache*
[ -x /etc/init.d/rpcd ] && /etc/init.d/rpcd restart
exit 0
"""


def build(args) -> list[Path]:
    root = Path(args.root).resolve()
    files_dir = root / "files"
    if not files_dir.is_dir():
        sys.exit(f"error: {files_dir} not found")

    makefile = root / "Makefile"
    version = args.version or read_make_var(makefile, "PKG_VERSION", VERSION)
    release = args.release or read_make_var(makefile, "PKG_RELEASE", RELEASE)
    pkgver = f"{version}-r{release}"

    meta = {
        "description": "HomeProxy master switch page for LuCI",
        "url": read_make_var(makefile, "PKG_SOURCE_URL", ""),
        "license": "MIT",
        "maintainer": read_make_var(makefile, "PKG_MAINTAINER", ""),
        "depends": [],  # left empty on purpose: `apk add <file>` must work offline
        "postinst": POSTINST,
        "postdeinst": POSTDEINST,
    }

    mtime = int(os.environ.get("SOURCE_DATE_EPOCH") or 0)
    out = root / "build"
    if not out.exists():
        out.mkdir(parents=True)
    produced = []

    flavours = {"v3": build_v3, "v2": build_v2}
    wanted = args.flavour.split(",") if args.flavour != "both" else ["v3", "v2"]
    for flavour in wanted:
        blob = flavours[flavour](files_dir, pkgver, meta, mtime)
        suffix = "" if flavour == "v3" else "-v2"
        target = out / f"{PKG_NAME}_{version}-{release}_all{suffix}.apk"
        target.write_bytes(blob)
        produced.append(target)
    return produced


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", "-R", default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--version", "-v")
    ap.add_argument("--release", "-r")
    ap.add_argument("--flavour", "-t", default="v3",
                    help="v3 (OpenWrt 25, default), v2 (legacy) or v3,v2")
    ns = ap.parse_args()
    for path in build(ns):
        print(f"built: {path}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
