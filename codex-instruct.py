#!/usr/bin/env python3
"""Install and roll back the project's two independent Codex instruction layers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_SOURCE = PROJECT_ROOT / "prompts" / "gpt-5.6-sol-custom-v1.md"
DEFAULT_DEVELOPER_SOURCE = PROJECT_ROOT / "prompts" / "gpt-5.6-sol-custom-v1.developer.md"
DEFAULT_MODEL_FILENAME = DEFAULT_MODEL_SOURCE.name
DEFAULT_DEVELOPER_FILENAME = DEFAULT_DEVELOPER_SOURCE.name
STATE_FILENAME = ".gpt56-sol-custom-state.json"
STATE_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_PATTERN = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


@dataclass(frozen=True)
class Assignment:
    key: str
    start: int
    end: int
    raw: str


@dataclass(frozen=True)
class PromptBundle:
    model_text: str
    developer_text: str
    model_filename: str
    developer_filename: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def safe_filename(value: object) -> bool:
    if not isinstance(value, str):
        return False
    path = Path(value)
    return (
        bool(value)
        and path.name == value
        and "/" not in value
        and "\\" not in value
        and value.lower().endswith(".md")
    )


def config_state_path(config_path: Path) -> Path:
    return config_path.parent / STATE_FILENAME


def config_backup_path(config_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return config_path.with_name(f"{config_path.name}.gpt56-sol-custom.bak_{stamp}")


def line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def top_level_table_start(lines: list[str]) -> int:
    multiline_delimiter: str | None = None
    for index, line in enumerate(lines):
        if multiline_delimiter is not None:
            if multiline_delimiter in line:
                multiline_delimiter = None
            continue
        for delimiter in ("'''", '"""'):
            if line.count(delimiter) % 2 == 1:
                multiline_delimiter = delimiter
                break
        if line.lstrip().startswith("["):
            return index
    return len(lines)


def find_assignment(text: str, key: str) -> Assignment | None:
    lines = text.splitlines(keepends=True)
    table_start = top_level_table_start(lines)
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines[:table_start]):
        match = pattern.match(line)
        if match is None:
            continue
        tail = line[match.end() :].lstrip()
        end = index + 1
        if tail.startswith(("'''", '"""')):
            delimiter = tail[:3]
            if delimiter not in tail[3:]:
                while end < len(lines):
                    if delimiter in lines[end]:
                        end += 1
                        break
                    end += 1
                else:
                    raise ValueError(f"未闭合的 TOML 多行字段: {key}")
        return Assignment(key=key, start=index, end=end, raw="".join(lines[index:end]))
    return None


def top_level_value(text: str, key: str) -> object | None:
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    return parsed.get(key)


def replace_assignment(text: str, key: str, replacement: str | None) -> str:
    lines = text.splitlines(keepends=True)
    assignment = find_assignment(text, key)
    newline = line_ending(text)
    if assignment is None:
        if replacement is None:
            return text
        insert_at = top_level_table_start(lines)
        rendered = replacement.replace("\n", newline)
        if not rendered.endswith(newline):
            rendered += newline
        lines.insert(insert_at, rendered)
        return "".join(lines)

    if replacement is None:
        del lines[assignment.start : assignment.end]
        return "".join(lines)

    rendered = replacement.replace("\n", newline)
    if not rendered.endswith(newline):
        rendered += newline
    lines[assignment.start : assignment.end] = [rendered]
    return "".join(lines)


def format_developer_assignment(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if "'''" not in normalized:
        # TOML trims only the first newline after the opening delimiter, so
        # leaving the closing delimiter adjacent preserves the source ending.
        return "developer_instructions = '''\n" + normalized + "'''"
    # JSON is a valid TOML basic string and safely handles arbitrary Markdown.
    return "developer_instructions = " + json.dumps(normalized, ensure_ascii=False)


def load_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
        return None
    for key in ("model_filename", "developer_filename"):
        if not safe_filename(value.get(key, "")):
            return None
    for key in ("model_sha256", "developer_sha256"):
        if not isinstance(value.get(key), str) or not SHA256_PATTERN.fullmatch(value[key]):
            return None
    for key in ("previous_model_assignment", "previous_developer_assignment"):
        if value.get(key) is not None and not isinstance(value.get(key), str):
            return None
    for key in ("model_existed_before", "developer_existed_before"):
        if not isinstance(value.get(key), bool):
            return None
    return value


def save_state(path: Path, state: dict[str, object]) -> None:
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def read_source(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"提示词文件不存在: {path}")
    value = path.read_text(encoding="utf-8")
    if not value.strip():
        raise ValueError(f"提示词文件为空: {path}")
    return value


def prompt_bundle(args: argparse.Namespace) -> PromptBundle:
    model_path = Path(args.file).expanduser().resolve() if args.file else DEFAULT_MODEL_SOURCE
    developer_path = (
        Path(args.developer_file).expanduser().resolve()
        if args.developer_file
        else DEFAULT_DEVELOPER_SOURCE
    )
    model_filename = args.name or DEFAULT_MODEL_FILENAME
    developer_filename = args.developer_name or DEFAULT_DEVELOPER_FILENAME
    if not safe_filename(model_filename) or not safe_filename(developer_filename):
        raise ValueError("提示词文件名必须是单层 Markdown 文件名")
    return PromptBundle(
        model_text=read_source(model_path),
        developer_text=read_source(developer_path),
        model_filename=model_filename,
        developer_filename=developer_filename,
    )


def selected_codex_dir(value: str | None) -> Path:
    raw = value or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(raw).expanduser().resolve()


def ensure_destination(destination: Path, content: str) -> bool:
    """Return whether the file existed; refuse to overwrite unrelated user data."""
    if not destination.exists():
        return False
    if not destination.is_file():
        raise RuntimeError(f"目标不是普通文件: {destination}")
    if destination.read_text(encoding="utf-8") != content:
        raise RuntimeError(f"目标文件已存在且内容不同，未覆盖: {destination}")
    return True


def expected_model_value(filename: str) -> str:
    return f"./{filename}"


def apply_bundle(args: argparse.Namespace) -> int:
    try:
        bundle = prompt_bundle(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    codex_dir = selected_codex_dir(args.codex_dir)
    config_path = codex_dir / "config.toml"
    if not config_path.is_file():
        print(f"[错误] 找不到配置文件: {config_path}", file=sys.stderr)
        return 2
    try:
        original_config = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"[错误] 读取配置失败: {exc}", file=sys.stderr)
        return 2

    model_destination = codex_dir / bundle.model_filename
    developer_destination = codex_dir / bundle.developer_filename
    state_path = config_state_path(config_path)
    old_state = load_state(state_path)
    try:
        model_existed = ensure_destination(model_destination, bundle.model_text)
        developer_existed = ensure_destination(developer_destination, bundle.developer_text)
        model_assignment = find_assignment(original_config, "model_instructions_file")
        developer_assignment = find_assignment(original_config, "developer_instructions")
        if old_state is not None:
            if top_level_value(original_config, "model_instructions_file") != expected_model_value(
                str(old_state["model_filename"])
            ):
                raise RuntimeError("现有 model_instructions_file 已脱离本项目管理，停止覆盖")
            if sha256_text(str(top_level_value(original_config, "developer_instructions") or "")) != str(
                old_state["developer_sha256"]
            ):
                raise RuntimeError("现有 developer_instructions 已脱离本项目管理，停止覆盖")
            previous_model = old_state.get("previous_model_assignment")
            previous_developer = old_state.get("previous_developer_assignment")
        else:
            previous_model = model_assignment.raw if model_assignment else None
            previous_developer = developer_assignment.raw if developer_assignment else None

        new_config = replace_assignment(
            original_config,
            "model_instructions_file",
            f'model_instructions_file = "{expected_model_value(bundle.model_filename)}"',
        )
        new_config = replace_assignment(
            new_config,
            "developer_instructions",
            format_developer_assignment(bundle.developer_text),
        )
        parsed = tomllib.loads(new_config)
        if parsed.get("model_instructions_file") != expected_model_value(bundle.model_filename):
            raise ValueError("写入后的 model_instructions_file 校验失败")
        if parsed.get("developer_instructions") != bundle.developer_text:
            raise ValueError("写入后的 developer_instructions 校验失败")
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    state = {
        "version": STATE_VERSION,
        "model_filename": bundle.model_filename,
        "developer_filename": bundle.developer_filename,
        "model_sha256": sha256_text(bundle.model_text),
        "developer_sha256": sha256_text(bundle.developer_text),
        "model_existed_before": model_existed,
        "developer_existed_before": developer_existed,
        "previous_model_assignment": previous_model,
        "previous_developer_assignment": previous_developer,
    }
    print(f"[+] CODEX_HOME: {codex_dir}")
    print(f"[+] model_instructions_file -> ./{bundle.model_filename}")
    print(f"[+] developer_instructions <- {bundle.developer_filename}")
    if args.dry_run:
        print("[dry-run] 未写入文件")
        return 0

    backup = config_backup_path(config_path)
    created_files: list[Path] = []
    try:
        shutil.copy2(config_path, backup)
        if not model_existed:
            atomic_write_text(model_destination, bundle.model_text)
            created_files.append(model_destination)
        if not developer_existed:
            atomic_write_text(developer_destination, bundle.developer_text)
            created_files.append(developer_destination)
        atomic_write_text(config_path, new_config)
        save_state(state_path, state)
    except (OSError, UnicodeError) as exc:
        atomic_write_text(config_path, original_config)
        for path in created_files:
            if path.exists():
                path.unlink()
        print(f"[错误] 安装事务回滚: {exc}", file=sys.stderr)
        return 2
    print(f"[+] 配置快照: {backup.name}")
    print("[+] 双层提示词安装完成")
    return 0


def restore_assignment_if_owned(
    text: str,
    key: str,
    expected_value: str,
    previous_assignment: str | None,
    developer: bool = False,
) -> tuple[str, bool, str]:
    current_value = top_level_value(text, key)
    if developer:
        owned = isinstance(current_value, str) and sha256_text(current_value) == expected_value
    else:
        owned = current_value == expected_value
    if not owned:
        return text, False, "配置字段已被用户修改，保留当前值"
    return replace_assignment(text, key, previous_assignment), True, "配置字段已恢复"


def reset_bundle(args: argparse.Namespace) -> int:
    codex_dir = selected_codex_dir(args.codex_dir)
    config_path = codex_dir / "config.toml"
    state_path = config_state_path(config_path)
    state = load_state(state_path)
    if state is None:
        print("[=] 没有可回滚的 custom-v1 安装状态")
        return 0
    if not config_path.is_file():
        print(f"[错误] 找不到配置文件: {config_path}", file=sys.stderr)
        return 2
    try:
        original_config = config_path.read_text(encoding="utf-8")
        new_config, model_done, model_status = restore_assignment_if_owned(
            original_config,
            "model_instructions_file",
            expected_model_value(str(state["model_filename"])),
            state.get("previous_model_assignment"),
        )
        new_config, developer_done, developer_status = restore_assignment_if_owned(
            new_config,
            "developer_instructions",
            str(state["developer_sha256"]),
            state.get("previous_developer_assignment"),
            developer=True,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"[错误] 回滚前检查失败: {exc}", file=sys.stderr)
        return 2

    files_done = True
    file_status: list[str] = []
    for filename, digest_key, existed_key in (
        (str(state["model_filename"]), "model_sha256", "model_existed_before"),
        (str(state["developer_filename"]), "developer_sha256", "developer_existed_before"),
    ):
        path = codex_dir / filename
        if bool(state[existed_key]):
            file_status.append(f"保留预存在文件: {filename}")
            continue
        if not path.exists():
            file_status.append(f"文件已不存在: {filename}")
            continue
        if path.is_file() and sha256_file(path) == str(state[digest_key]):
            file_status.append(f"移除本项目文件: {filename}")
        else:
            files_done = False
            file_status.append(f"保留用户修改文件: {filename}")

    if not args.yes and not args.dry_run:
        answer = input("确认回滚双层提示词？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("[=] 已取消")
            return 0
    print(f"[+] {model_status}")
    print(f"[+] {developer_status}")
    for status in file_status:
        print(f"[+] {status}")
    if args.dry_run:
        print("[dry-run] 未写入文件")
        return 0

    if new_config != original_config:
        atomic_write_text(config_path, new_config)
    for filename, digest_key, existed_key in (
        (str(state["model_filename"]), "model_sha256", "model_existed_before"),
        (str(state["developer_filename"]), "developer_sha256", "developer_existed_before"),
    ):
        path = codex_dir / filename
        if not bool(state[existed_key]) and path.is_file() and sha256_file(path) == str(state[digest_key]):
            path.unlink()

    complete = model_done and developer_done and files_done
    if complete and state_path.exists():
        state_path.unlink()
        print("[+] 回滚完成，状态文件已移除")
    else:
        print("[!] 检测到用户修改，状态文件保留以便后续复核")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安装或回滚 custom-v1 双层 Codex 提示词")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="安装双层提示词")
    action.add_argument("--reset", action="store_true", help="回滚本项目管理的两个字段")
    parser.add_argument("--codex-dir", help="目标 CODEX_HOME")
    parser.add_argument("--file", help="自定义 model prompt Markdown")
    parser.add_argument("--developer-file", help="自定义 developer prompt Markdown")
    parser.add_argument("--name", help="目标 model prompt 文件名")
    parser.add_argument("--developer-name", help="目标 developer prompt 文件名")
    parser.add_argument("--dry-run", action="store_true", help="只显示动作，不写文件")
    parser.add_argument("--yes", action="store_true", help="跳过回滚确认")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.apply and not args.reset:
        parser.print_help()
        return 2
    if args.dry_run and not args.apply and not args.reset:
        parser.error("--dry-run 需要 --apply 或 --reset")
    return apply_bundle(args) if args.apply else reset_bundle(args)


if __name__ == "__main__":
    raise SystemExit(main())
