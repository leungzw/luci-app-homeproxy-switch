#!/usr/bin/env python3
"""Build an OpenWrt .ipk directly from the package directory.

No SDK and no cross toolchain needed: this package ships only shell scripts,
JSON menu definitions and a LuCI JavaScript view, so hand-assembling the
archive is enough -- and it is what CI does too.

Two container formats are produced:

  ar       classic installable = debian-binary + control.tar.gz + data.tar.gz
           inside an ar archive.  This is what OpenWrt <= 23.05 expects.
  tar.gz   OpenWrt 24.10+ / ImmortalWrt 24.10 users: their opkg no longer
           reads ar, it unpacks gzip( tar( debian-binary, control.tar.gz,
           data.tar.gz ) ) instead.

    python3 tools/build_ipk.py            # both formats, version from Makefile
    python3 tools/build_ipk.py -r 2       # override release number
"""

import argparse
import gzip
import io
import hashlib
import os
import re
import sys
import tarfile
from pathlib import Path

PKG_NAME = "luci-app-homeproxy-switch"
ARCHIVE_MODE = 0o100644
SCRIPT_MODE = 0o100755
DIR_MODE = 0o40755
ENDIAN_MAGIC = b"`\n"


def read_make_var(makefile: Path, name: str, fallback: str) -> str:
    m = re.search(rf"^{name}\s*:?=\s*(.+?)\s*$", makefile.read_text(), re.M)
    return m.group(1) if m else fallback


def collect_files(files_dir: Path):
    entries = []
    for path in sorted(files_dir.rglob("*")):
        if path.is_file():
            entries.append((path, "./" + str(path.relative_to(files_dir))))
    return entries


def dir_specs(arcnames):
    """Parent directory entries.

    opkg unpacks the data tarball entry by entry and does not create missing
    parents on its own -- leaving the directories out makes it fail with
    `wfopen: <path>: No such file or directory` while still reporting the
    package as installed.  Official packages always ship these entries.
    """
    seen = {"."}
    for arcname in arcnames:
        parts = arcname.split("/")
        for i in range(2, len(parts)):
            seen.add("/".join(parts[:i]))
    return [{"name": name, "mode": DIR_MODE, "isdir": True} for name in sorted(seen)]


def make_targz(target: Path, specs):
    """specs: dicts with name + either src (file) or content (bytes) or isdir."""
    with tarfile.open(target, "w:gz", format=tarfile.USTAR_FORMAT) as tf:
        for spec in specs:
            info = tarfile.TarInfo(spec["name"])
            info.mtime = 0
            info.mode = spec.get("mode", ARCHIVE_MODE)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if spec.get("isdir"):
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif "content" in spec:
                payload = spec["content"]
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
            else:
                src = Path(spec["src"])
                info.size = src.stat().st_size
                with open(src, "rb") as fh:
                    tf.addfile(info, fh)


def control_text(version, release, installed_kb, maintainer, source_url, description) -> str:
    lines = [
        f"Package: {PKG_NAME}",
        f"Version: {version}-{release}",
        "Depends: libc, luci-base, rpcd-mod-file",
        f"Provides: {PKG_NAME}",
        "Section: luci",
        f"Architecture: {os.environ.get('IPK_ARCH', 'all')}",
        f"Installed-Size: {installed_kb}",
        f"Maintainer: {maintainer}",
        f"Source: {source_url}",
        f"SourceName: {PKG_NAME}",
        "License: MIT",
        "LicenseFiles: LICENSE",
        f"Description: {description[0]}",
    ]
    lines += [f" {line}" if line else " ." for line in description[1:]]
    return "\n".join(lines) + "\n"


def ar_member(name: str, payload: bytes) -> bytes:
    header = f"{name:<16}{0:<12}{0:<6}{0:<6}{0o644:<8}{len(payload):<10}".encode()
    header += ENDIAN_MAGIC
    blob = header + payload
    if len(blob) % 2:
        blob += b"\n"
    return blob


def build(args) -> list:
    root = Path(args.root).resolve()
    files_dir = root / "files"
    if not files_dir.is_dir():
        sys.exit(f"error: {files_dir} not found")

    makefile = root / "Makefile"
    version = args.version or read_make_var(makefile, "PKG_VERSION", "1.0.0")
    release = args.release or read_make_var(makefile, "PKG_RELEASE", "1")
    maintainer = read_make_var(makefile, "PKG_MAINTAINER", "nobody <nobody@example.com>")
    source_url = read_make_var(makefile, "PKG_SOURCE_URL", "")
    description = read_make_var(makefile, "TITLE", "HomeProxy switch for LuCI")

    entries = collect_files(files_dir)
    if not entries:
        sys.exit("error: nothing to package")

    installed_bytes = sum(p.stat().st_size for p, _ in entries)
    md5_lines = []
    for src, arcname in entries:
        digest = hashlib.md5(src.read_bytes()).hexdigest()
        md5_lines.append(f"{digest}  {arcname[2:]}")

    out_dir = root / "build"
    out_dir.mkdir(exist_ok=True)
    data_tgz = out_dir / "data.tar.gz"
    control_tgz = out_dir / "control.tar.gz"

    make_targz(data_tgz, dir_specs([a for _, a in entries]) + [
        {"name": arcname, "src": src,
         "mode": SCRIPT_MODE if "bin/" in arcname else ARCHIVE_MODE}
        for src, arcname in entries
    ])

    postinst = b"""#!/bin/sh
rm -f /tmp/luci-indexcache* /tmp/luci-modulecache*
[ -x /etc/init.d/rpcd ] && /etc/init.d/rpcd restart
exit 0
"""
    control = control_text(
        version, release, max(1, installed_bytes // 1024), maintainer, source_url,
        [
            description,
            "Adds a Proxy Switch page under Services -> HomeProxy with three buttons",
            "(full / self / off) that control both the running state and the boot",
            "autostart of the HomeProxy service.",
        ],
    ).encode()

    make_targz(control_tgz, [
        {"name": "./control", "content": control},
        {"name": "./md5sums", "content": ("\n".join(md5_lines) + "\n").encode()},
        {"name": "./postinst", "content": postinst, "mode": SCRIPT_MODE},
        {"name": "./postrm", "content": postinst, "mode": SCRIPT_MODE},
    ])

    control_blob = control_tgz.read_bytes()
    data_blob = data_tgz.read_bytes()
    data_tgz.unlink()
    control_tgz.unlink()

    stamp = f"{version}-{release}"
    built = []

    if args.format in ("both", "tar.gz"):
        # OpenWrt 24.10+ shape: gzip( tar( debian-binary, control, data ) )
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as outer:
            for name, payload in (("debian-binary", b"2.0\n"),
                                  ("control.tar.gz", control_blob),
                                  ("data.tar.gz", data_blob)):
                info = tarfile.TarInfo("./" + name)
                info.size = len(payload)
                info.mtime = 0
                info.mode = ARCHIVE_MODE
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                outer.addfile(info, io.BytesIO(payload))
        path = out_dir / f"{PKG_NAME}_{stamp}_all.ipk"
        path.write_bytes(gzip.compress(raw.getvalue(), mtime=0))
        built.append(path)

    if args.format in ("both", "ar"):
        payload = b"!<arch>\n"
        payload += ar_member("debian-binary", b"2.0\n")
        payload += ar_member("control.tar.gz", control_blob)
        payload += ar_member("data.tar.gz", data_blob)
        path = out_dir / f"{PKG_NAME}_{stamp}_all.legacy.ipk"
        path.write_bytes(payload)
        built.append(path)

    return built


def main():
    ap = argparse.ArgumentParser(description=f"Build {PKG_NAME}.ipk")
    ap.add_argument("--root", default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--version", "-v")
    ap.add_argument("--release", "-r")
    ap.add_argument("--format", "-f", choices=["both", "ar", "tar.gz"], default="both")
    ns = ap.parse_args()
    for pkg in build(ns):
        print(f"built: {pkg}  ({pkg.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
