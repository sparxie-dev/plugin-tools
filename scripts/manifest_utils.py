"""plugin.json 的静态校验，规则对齐 Sparxie 主程序（src/Sparxie.Infrastructure/Plugins 里的 PluginManifest / PluginSettingsSchema）。

主程序是最终裁判：这里的目的是让插件作者在 CI 里就发现问题。主程序改了规则时要同步改这里。只用标准库。
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

MANIFEST_NAME = "plugin.json"
MODULE_NAME = "module.dll"
MAX_MANIFEST_BYTES = 64 * 1024
ABI_MAJOR = 1

ID_PATTERN = re.compile(r"ext\.[a-z0-9-]{2,32}\.[a-z0-9-]{2,32}")
FIELD_KEY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
# 主程序只接受不带构建元数据的版本号用于升级/降级判断；发布 tag 为 v<version>
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")

CAP_BITS = {"createsProcess": 1, "injects": 2, "needsArgs": 4, "hotParams": 8, "disableTouchUi": 16}
FORBIDDEN_EXE_NAMES = {
    "explorer.exe", "svchost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "rundll32.exe", "conhost.exe",
    "wininit.exe", "services.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "taskmgr.exe", "regedit.exe",
    "mshta.exe", "wscript.exe", "cscript.exe", "msiexec.exe", "dwm.exe",
}
FORBIDDEN_REGISTRY_SEGMENTS = {
    "run", "runonce", "runonceex", "runservices", "runservicesonce", "policies", "winlogon",
    "image file execution options", "classes", "wow6432node", "microsoft",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".ico"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class ManifestError(Exception):
    pass


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise ManifestError(f"找不到 {path}")
    raw = path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("plugin.json 超过 64 KB")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ManifestError("plugin.json 不能带 UTF-8 BOM")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"plugin.json 无法解析: {error}") from error
    if not isinstance(data, dict):
        raise ManifestError("plugin.json 顶层必须是对象")
    return data


def _is_text(value, max_length: int, min_length: int = 1) -> bool:
    return isinstance(value, str) and min_length <= len(value) <= max_length and value.strip() != ""


def _optional_text(errors: list, data: dict, name: str, max_length: int) -> None:
    value = data.get(name)
    if value is not None and not (isinstance(value, str) and len(value) <= max_length):
        errors.append(f"{name} 必须是不超过 {max_length} 个字符的字符串")


def is_allowed_url(url) -> bool:
    """只允许 https，且不能指向本机或内网字面地址（与主程序一致）。"""
    if not _is_text(url, 2048):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified)


def is_plain_file_name(name, extensions: set) -> bool:
    return (
        _is_text(name, 64)
        and not any(ch in name for ch in '/\\:*?"<>|')
        and Path(name).suffix.lower() in extensions
    )


def _check_registry_prefix(prefix) -> bool:
    if not _is_text(prefix, 255) or prefix != prefix.strip():
        return False
    segments = prefix.split("\\")
    if len(segments) < 3 or segments[0].lower() != "software":
        return False
    if any(s.strip() == "" or s in (".", "..") for s in segments):
        return False
    return not any(s.lower() in FORBIDDEN_REGISTRY_SEGMENTS for s in segments[1:])


def _check_registry_lookup(item) -> bool:
    if not isinstance(item, dict):
        return False
    key, value, sub = item.get("key"), item.get("value"), item.get("subdirectory")
    if item.get("hive", "HKCU") not in ("HKCU", "HKLM"):
        return False
    if not _is_text(key, 260) or ".." in key or key.startswith("\\") or key.endswith("\\"):
        return False
    segments = key.split("\\")
    if segments.count("*") > 2 or any("*" in s and s != "*" for s in segments):
        return False
    if not _is_text(value, 64):
        return False
    return sub is None or (isinstance(sub, str) and len(sub) <= 64 and ".." not in sub and not sub.startswith(("\\", "/")) and ":" not in sub)


def _check_settings_schema(errors: list, schema) -> None:
    if schema is None:
        return
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        errors.append("settingsSchema 必须是含 fields 数组的对象")
        return
    fields = schema["fields"]
    if len(fields) > 64:
        errors.append("settingsSchema 字段超过 64 个")
    seen: set = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            errors.append(f"fields[{index}] 必须是对象")
            continue
        key = field.get("key")
        if not (isinstance(key, str) and FIELD_KEY_PATTERN.fullmatch(key)) or key in seen:
            errors.append(f"字段 key 非法或重复: {key}")
            continue
        if not _is_text(field.get("label"), 64):
            errors.append(f"字段 {key} 的 label 为空或过长")
        for name, limit in (("group", 32), ("hint", 200)):
            value = field.get(name)
            if isinstance(value, str) and len(value) > limit:
                errors.append(f"字段 {key} 的 {name} 超过 {limit} 个字符")
        when = field.get("showWhen")
        if when is not None and not (isinstance(when, dict) and when.get("key") in seen and "equals" in when):
            errors.append(f"字段 {key} 的 showWhen 必须引用前面已声明的字段")
        kind, default = field.get("type"), field.get("default")
        if kind == "bool":
            if not isinstance(default, bool):
                errors.append(f"字段 {key} 的 default 必须是 bool")
        elif kind == "int":
            low, high = field.get("min"), field.get("max")
            numbers = (low, high, default)
            if not all(isinstance(n, int) and not isinstance(n, bool) for n in numbers) or not low <= default <= high:
                errors.append(f"字段 {key} 的 min / max / default 非法")
            else:
                step = field.get("step", 1)
                if not isinstance(step, int) or not 1 <= step <= high - low + 1:
                    errors.append(f"字段 {key} 的 step 非法")
        elif kind == "enum":
            options = field.get("options")
            if not isinstance(options, list) or not 1 <= len(options) <= 32:
                errors.append(f"字段 {key} 的 options 数量非法")
            else:
                values = [o.get("value") for o in options if isinstance(o, dict)]
                if (len(values) != len(options) or len(set(map(str, values))) != len(values)
                        or not all(_is_text(v, 64) for v in values)
                        or not all(_is_text(o.get("label"), 64) for o in options if isinstance(o, dict))):
                    errors.append(f"字段 {key} 的选项非法或重复")
                elif default not in values:
                    errors.append(f"字段 {key} 的 default 必须是某个选项的 value")
        elif kind == "string":
            limit = field.get("maxLength", 256)
            if not isinstance(limit, int) or not 1 <= limit <= 256:
                errors.append(f"字段 {key} 的 maxLength 非法")
            elif not isinstance(default, str) or len(default) > limit:
                errors.append(f"字段 {key} 的 default 必须是不超过 maxLength 的字符串")
        else:
            errors.append(f"字段 {key} 的 type 非法: {kind}")
        seen.add(key)


def validate_manifest(data: dict) -> list:
    """返回错误列表；空列表表示通过。"""
    errors: list = []
    plugin_id = data.get("id")
    if not (isinstance(plugin_id, str) and ID_PATTERN.fullmatch(plugin_id)):
        errors.append(f"id 必须形如 ext.<厂商>.<游戏>（小写字母、数字、连字符，每段 2-32 个字符）: {plugin_id}")
    if not _is_text(data.get("displayName"), 64):
        errors.append("displayName 为空或超过 64 个字符")
    if data.get("abiMajor") != ABI_MAJOR:
        errors.append(f"abiMajor 必须是 {ABI_MAJOR}")
    version = data.get("version")
    if not (isinstance(version, str) and VERSION_PATTERN.fullmatch(version)):
        errors.append(f"version 必须形如 1.2.3 或 1.2.3-rc.1（发布和升级判断都依赖它）: {version}")

    exe_names = data.get("exeNames")
    if not isinstance(exe_names, list) or not 1 <= len(exe_names) <= 8:
        errors.append("exeNames 需要 1-8 项")
    else:
        for name in exe_names:
            if not is_plain_file_name(name, {".exe"}) or name.lower() in FORBIDDEN_EXE_NAMES:
                errors.append(f"exeNames 含非法名称: {name}")

    caps = data.get("caps", [])
    if not isinstance(caps, list) or any(cap not in CAP_BITS for cap in caps):
        errors.append(f"caps 只能包含 {', '.join(CAP_BITS)}")

    hooks = data.get("registryHooks")
    if hooks is not None:
        keys = hooks.get("keys") if isinstance(hooks, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 8 or not all(map(_check_registry_prefix, keys)):
            errors.append("registryHooks.keys 需要 1-8 个合法的 HKCU 前缀（Software\\厂商\\游戏 这样至少三段，且不含系统敏感段）")

    for name, limit in (("author", 64), ("description", 300), ("privacyNote", 300)):
        _optional_text(errors, data, name, limit)

    links = data.get("links")
    if links is not None:
        if not isinstance(links, list) or len(links) > 4 or not all(
                isinstance(link, dict) and _is_text(link.get("title"), 32) and is_allowed_url(link.get("url")) for link in links):
            errors.append("links 最多 4 项，每项需要 title（≤32）和 https 的 url")

    for name in ("icon", "background"):
        value = data.get(name)
        if value is not None and not is_plain_file_name(value, IMAGE_EXTENSIONS):
            errors.append(f"{name} 必须是插件目录里的图片文件名（{' '.join(sorted(IMAGE_EXTENSIONS))}，不能带路径）")

    auto_find = data.get("autoFind")
    if auto_find is not None:
        if not isinstance(auto_find, dict):
            errors.append("autoFind 必须是对象")
        else:
            names = auto_find.get("uninstallNames") or []
            if not isinstance(names, list) or len(names) > 8 or not all(_is_text(n, 64, 2) for n in names):
                errors.append("autoFind.uninstallNames 最多 8 项，每项 2-64 个字符")
            lookups = auto_find.get("registry") or []
            if not isinstance(lookups, list) or len(lookups) > 8 or not all(map(_check_registry_lookup, lookups)):
                errors.append("autoFind.registry 最多 8 项；hive 只能是 HKCU / HKLM；key 里的 * 必须是完整的一段且最多 2 个")

    url = data.get("backgroundUrl")
    if url is not None and not is_allowed_url(url):
        errors.append("backgroundUrl 必须是 https 地址，且不能指向本机或内网")

    api = data.get("backgroundApi")
    if api is not None:
        ok = (
            isinstance(api, dict)
            and is_allowed_url(api.get("url"))
            and _is_text(api.get("path"), 128)
            and all(api.get(n) is None or (isinstance(api.get(n), str) and len(api[n]) <= 128) for n in ("itemPath", "videoPath"))
            and api.get("method") in (None, "GET", "POST")
            and (api.get("body") is None or (isinstance(api["body"], dict) and len(json.dumps(api["body"], ensure_ascii=False, separators=(",", ":"))) <= 4096))
        )
        if not ok:
            errors.append("backgroundApi 需要 https 的 url 和 path；method 只能是 GET / POST；body 必须是不超过 4 KB 的 JSON 对象")

    _check_settings_schema(errors, data.get("settingsSchema"))
    return errors


def asset_names(data: dict) -> list:
    """打包时要带上的图片文件名：manifest 声明的，加上约定名 icon.ico / icon.png。"""
    return [n for n in (data.get("icon"), data.get("background")) if n]


CONVENTIONAL_ICONS = ("icon.ico", "icon.png")
