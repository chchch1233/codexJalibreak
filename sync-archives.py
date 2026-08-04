#!/usr/bin/env python3
"""Create and verify the deterministic custom-v1 prompt archive."""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ARCHIVE_PATH = PROJECT_ROOT / "gpt-5.6-sol-custom-v1.zip"
SOURCES = (
    PROJECT_ROOT / "prompts" / "gpt-5.6-sol-custom-v1.md",
    PROJECT_ROOT / "prompts" / "gpt-5.6-sol-custom-v1.developer.md",
)


def archive_bytes(source: Path) -> bytes:
    return source.read_bytes()


def write_archive(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source in SOURCES:
            info = zipfile.ZipInfo(source.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, archive_bytes(source))


def archive_matches(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            expected_names = [source.name for source in SOURCES]
            return names == expected_names and all(
                archive.read(source.name) == archive_bytes(source) for source in SOURCES
            )
    except (OSError, zipfile.BadZipFile, KeyError):
        return False


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步 custom-v1 双提示词 ZIP")
    parser.add_argument("--check", action="store_true", help="只校验现有 ZIP")
    args = parser.parse_args(argv)
    missing = [str(path) for path in SOURCES if not path.is_file()]
    if missing:
        print("[错误] 缺少源文件: " + ", ".join(missing), file=sys.stderr)
        return 2
    if args.check:
        if not archive_matches(ARCHIVE_PATH):
            print(f"[错误] ZIP 与当前源文件不一致: {ARCHIVE_PATH}", file=sys.stderr)
            return 1
        print(f"[=] ZIP 校验通过: {ARCHIVE_PATH.name} sha256={sha256(ARCHIVE_PATH)}")
        return 0
    write_archive(ARCHIVE_PATH)
    if not archive_matches(ARCHIVE_PATH):
        print("[错误] 生成后的 ZIP 校验失败", file=sys.stderr)
        return 1
    print(f"[+] 已生成: {ARCHIVE_PATH.name} sha256={sha256(ARCHIVE_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
