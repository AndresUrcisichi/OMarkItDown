#!/usr/bin/python
"""Edición mínima, privada y reversible del overlay JSONC de Omarchy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile

BEGIN = "  // BEGIN markitdown-omarchy (managed)"
END = "  // END markitdown-omarchy (managed)"
DEFAULT_ACTION = "$HOME/.config/omarchy/scripts/markitdown-convert"
FIELDS = {
    "icon": "󰈙",
    "label": "MarkItDown",
    "description": "Convert a local document to Markdown",
}


def command_for_path(path: str) -> str:
    """Return one shell word; no user-controlled text is interpreted as syntax."""
    if "\x00" in path:
        raise ValueError("la ruta de la acción contiene un byte NUL")
    return shlex.quote(path)


def block(action: str = DEFAULT_ACTION) -> str:
    values = {**FIELDS, "action": action}
    lines = [BEGIN, '  "trigger.markitdown": {']
    keys = tuple(values)
    for index, key in enumerate(keys):
        comma = "," if index < len(keys) - 1 else ""
        lines.append(f"    {json.dumps(key)}: {json.dumps(values[key], ensure_ascii=False)}{comma}")
    lines.extend(("  },", END))
    return "\n".join(lines) + "\n"


BLOCK = block()


def _reject_obvious_missing_separators(content: str) -> None:
    """Rechaza valores adyacentes; no implementa toda la gramática JSONC."""
    tokens: list[str] = []
    i = 1 if content.startswith("\ufeff") else 0
    while i < len(content):
        if content[i] == '"':
            tokens.append("S")
            i += 1
            escaped = False
            while i < len(content):
                if escaped:
                    escaped = False
                elif content[i] == "\\":
                    escaped = True
                elif content[i] == '"':
                    i += 1
                    break
                i += 1
        elif content.startswith("//", i):
            newline = content.find("\n", i + 2)
            tokens.append(" ")
            i = len(content) if newline < 0 else newline + 1
        elif content.startswith("/*", i):
            closing = content.find("*/", i + 2)
            tokens.append(" ")
            i = len(content) if closing < 0 else closing + 2
        else:
            tokens.append(content[i])
            i += 1
    simplified = "".join(tokens)
    value = r'(?:S|true\b|false\b|null\b|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|[}\]])'
    next_value = r'(?=S|\{|\[|true\b|false\b|null\b|-?\d)'
    if re.search(value + r"\s+" + next_value, simplified):
        raise ValueError("separador JSONC ausente entre valores")


def _scan(content: str) -> tuple[int, int, list[tuple[str, int]]]:
    """Validate JSONC lexical structure and locate its single root object."""
    strings: list[tuple[str, int]] = []
    stack: list[tuple[str, int]] = []
    root_open = root_close = -1
    i = 1 if content.startswith("\ufeff") else 0
    length = len(content)
    while i < length:
        char = content[i]
        if char == '"':
            if not stack:
                raise ValueError("contenido fuera del objeto raíz JSONC")
            start = i
            i += 1
            escaped = False
            while i < length:
                current = content[i]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    raw = content[start : i + 1]
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ValueError("string JSONC inválido") from exc
                    strings.append((value, i + 1))
                    i += 1
                    break
                elif ord(current) < 0x20:
                    raise ValueError("string JSONC inválido")
                i += 1
            else:
                raise ValueError("string JSONC sin cerrar")
            continue
        if char == "/" and i + 1 < length and content[i + 1] == "/":
            newline = content.find("\n", i + 2)
            i = length if newline < 0 else newline + 1
            continue
        if char == "/" and i + 1 < length and content[i + 1] == "*":
            closing = content.find("*/", i + 2)
            if closing < 0:
                raise ValueError("comentario JSONC sin cerrar")
            i = closing + 2
            continue
        if char in "{[":
            if not stack:
                if root_open >= 0 or char != "{":
                    raise ValueError("el overlay JSONC debe contener un único objeto raíz")
                root_open = i
            stack.append((char, i))
        elif char in "}]":
            expected = "{" if char == "}" else "["
            if not stack or stack[-1][0] != expected:
                raise ValueError("llaves o corchetes JSONC desbalanceados")
            stack.pop()
            if not stack:
                root_close = i
        elif not stack and not char.isspace():
            raise ValueError("contenido fuera del objeto raíz JSONC")
        i += 1
    if stack:
        raise ValueError("llaves o corchetes JSONC desbalanceados")
    if root_open < 0 or root_close < root_open:
        raise ValueError("el overlay JSONC no contiene un objeto raíz")
    _reject_obvious_missing_separators(content)
    return root_open, root_close, strings


def _skip_space_and_comments(content: str, position: int) -> int:
    i = position
    while i < len(content):
        if content[i].isspace():
            i += 1
        elif content.startswith("//", i):
            newline = content.find("\n", i + 2)
            i = len(content) if newline < 0 else newline + 1
        elif content.startswith("/*", i):
            closing = content.find("*/", i + 2)
            if closing < 0:
                raise ValueError("comentario JSONC sin cerrar")
            i = closing + 2
        else:
            break
    return i


def _has_trigger_property(content: str, strings: list[tuple[str, int]]) -> bool:
    return any(
        value == "trigger.markitdown"
        and _skip_space_and_comments(content, end) < len(content)
        and content[_skip_space_and_comments(content, end)] == ":"
        for value, end in strings
    )


def _managed_span(content: str) -> tuple[int, int] | None:
    begins = [match.start() for match in re.finditer(rf"(?m)^{re.escape(BEGIN)}$", content)]
    ends = [match.start() for match in re.finditer(rf"(?m)^{re.escape(END)}$", content)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise ValueError("bloque administrado parcial o duplicado; se conserva por seguridad")
    start = begins[0]
    finish = ends[0] + len(END)
    if finish < len(content) and content[finish] == "\n":
        finish += 1
    return start, finish


def add(content: str, action: str = DEFAULT_ACTION) -> str:
    opening, _closing, strings = _scan(content)
    expected = block(action)
    managed = _managed_span(content)
    if managed:
        if content[managed[0] : managed[1]] != expected:
            raise ValueError("el bloque administrado fue modificado; se conserva por seguridad")
        return content
    if _has_trigger_property(content, strings):
        raise ValueError("conflicto: trigger.markitdown ya existe y no pertenece al proyecto")
    prefix = content[: opening + 1]
    rest = content[opening + 1 :]
    return prefix + "\n" + expected + rest


def remove(content: str, action: str = DEFAULT_ACTION) -> str:
    _scan(content)
    managed = _managed_span(content)
    if managed is None:
        return content
    if content[managed[0] : managed[1]] != block(action):
        raise ValueError("el bloque administrado fue modificado; se conserva por seguridad")
    start, finish = managed
    if start > 0 and content[start - 1] == "\n":
        start -= 1
    return content[:start] + content[finish:]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("add", "remove"))
    parser.add_argument("path", type=Path)
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--action")
    action_group.add_argument("--action-path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup-suffix")
    args = parser.parse_args()
    try:
        action = args.action if args.action is not None else (
            command_for_path(args.action_path) if args.action_path is not None else DEFAULT_ACTION
        )
        content = args.path.read_text(encoding="utf-8") if args.path.exists() else "{}\n"
        updated = add(content, action) if args.mode == "add" else remove(content, action)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"MarkItDown-Omarchy: {exc}", file=sys.stderr)
        return 2
    if updated == content:
        print("menú: sin cambios")
    elif args.dry_run:
        print(f"menú: se modificaría {args.path}")
    else:
        try:
            if args.path.exists() and args.backup_suffix:
                backup = args.path.with_name(args.path.name + args.backup_suffix)
                shutil.copy2(args.path, backup)
                os.chmod(backup, 0o600)
                print(f"respaldo: {backup}")
            _atomic_write(args.path, updated)
        except OSError as exc:
            print(f"MarkItDown-Omarchy: no se pudo publicar el menú: {exc}", file=sys.stderr)
            return 2
        print(f"menú: actualizado {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
