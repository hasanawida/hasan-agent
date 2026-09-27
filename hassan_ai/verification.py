"""Checks of observable file effects; these do not prove a whole UI task succeeded."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def result_ok(tool: str, result: str) -> bool:
    if tool == "run":
        return re.match(r"^exit 0(?:\n|$)", result) is not None
    if tool in {"mcp", "blender"}:
        try:
            data = json.loads(result)
        except ValueError:
            # A truncated/invalid structured result cannot be reported as success.
            return False
        if not isinstance(data, dict):
            return False
        return not (data.get("is_error") or data.get("isError") or data.get("ok") is False
                    or data.get("exit", 0) != 0)
    return True


def prepare_file_check(tools, tool: str, args: dict) -> dict | None:
    if tool in {"write_file", "make_dir", "delete"}:
        return {"tool": tool, "path": tools.path(args["path"]), "content": args.get("content", ""),
                "trash": tools.trash_dir.resolve()}
    if tool in {"move", "copy"}:
        src = tools.media_file(args["src"]) or tools.path(args["src"])
        dst = tools.path(args["dst"])
        if dst.is_dir():
            dst = tools.path(str(dst / src.name))
        return {"tool": tool, "src": src, "dst": dst, "directory": src.is_dir()}
    return None


def digest(path: Path) -> bytes:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.digest()


def check_file_result(check: dict, result: str) -> tuple[bool, str]:
    tool = check["tool"]
    try:
        if tool == "write_file":
            ok = check["path"].read_bytes() == str(check["content"]).encode("utf-8")
            summary = "محتوى الملف يطابق النص المطلوب" if ok else "محتوى الملف لا يطابق النص المطلوب"
        elif tool == "make_dir":
            ok = check["path"].is_dir()
            summary = "تحققت من وجود المجلد" if ok else "المجلد غير موجود"
        elif tool == "delete":
            prefix = "moved to Hassan trash: "
            target = Path(result[len(prefix):]).resolve() if result.startswith(prefix) else None
            ok = (not check["path"].exists() and target is not None and
                  check["trash"] in target.parents and target.exists())
            summary = "الملف محفوظ في سلة الاسترجاع" if ok else "تعذر تأكيد النقل لسلة الاسترجاع"
        elif tool == "move":
            ok = not check["src"].exists() and check["dst"].exists()
            summary = "تحققت من وصول المسار الجديد واختفاء القديم" if ok else "النقل لم يكتمل"
        else:
            ok = check["src"].exists() and check["dst"].exists()
            if not check["directory"] and ok:
                ok = digest(check["src"]) == digest(check["dst"])
            summary = ("تحققت من وجود المجلدين؛ لم أفحص محتوى كل ملف" if check["directory"] else
                       "تحققت من تطابق محتوى النسخة والأصل") if ok else "النسخة مفقودة أو مختلفة"
        return bool(ok), summary
    except (OSError, ValueError) as exc:
        return False, f"تعذر التحقق: {type(exc).__name__}: {exc}"
