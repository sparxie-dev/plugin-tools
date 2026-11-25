"""构建并打包一个原生插件（只在 Windows 上运行）。

插件仓库只需要满足一个约定：根目录有 CMake 工程和 plugin.json，Release 构建产出唯一一个 module.dll。
步骤：cmake 配置与构建 → ctest → 加载 module.dll 与 plugin.json 交叉核对（和主程序加载时做的一样）→
打成 dist/<id>-<version>.zip（内含 <id>/ 目录）并生成 SHA-256 清单。

  python build_plugin.py --repo . [--out dist] [--build-dir build] [--cmake-args "-DFOO=1"] [--skip-build]
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from manifest_utils import (ABI_MAJOR, CAP_BITS, CONVENTIONAL_ICONS, MANIFEST_NAME, MODULE_NAME, ManifestError,
                            asset_names, load_manifest, validate_manifest)

TEXT_FILES = ("NOTICE.md", "README.md", "LICENSE")
ABI_MINOR = 0


class SpxStr(ctypes.Structure):
    _fields_ = [("p", ctypes.c_void_p), ("chars", ctypes.c_uint32)]

    def text(self) -> str:
        return ctypes.wstring_at(self.p, self.chars) if self.p else ""


class SpxModuleInfo(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("abi_major", ctypes.c_uint32), ("abi_minor", ctypes.c_uint32),
                ("id", SpxStr), ("display_name", SpxStr), ("version", SpxStr), ("caps", ctypes.c_uint32),
                ("exe_names", ctypes.POINTER(SpxStr)), ("exe_count", ctypes.c_uint32)]


class SpxModuleApiHead(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("abi_major", ctypes.c_uint32), ("abi_minor", ctypes.c_uint32),
                ("get_info", ctypes.c_void_p)]


def probe_module(dll: Path) -> dict:
    """加载刚构建出来的 module.dll，调用 sparxie_module_query 和 get_info（不创建会话，不启动任何东西）。"""
    library = ctypes.CDLL(str(dll.resolve()))
    try:
        query = library.sparxie_module_query
    except AttributeError as error:
        raise ManifestError("module.dll 没有导出 sparxie_module_query") from error
    query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(SpxModuleApiHead))]
    query.restype = ctypes.c_int
    api = ctypes.POINTER(SpxModuleApiHead)()
    code = query(ABI_MAJOR, ABI_MINOR, ctypes.byref(api))
    if code != 0 or not api:
        raise ManifestError(f"sparxie_module_query 返回 {code}")
    get_info = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.POINTER(SpxModuleInfo)))(api.contents.get_info)
    info_pointer = ctypes.POINTER(SpxModuleInfo)()
    code = get_info(ctypes.byref(info_pointer))
    if code != 0 or not info_pointer:
        raise ManifestError(f"get_info 返回 {code}")
    info = info_pointer.contents
    return {
        "abiMajor": info.abi_major,
        "id": info.id.text(),
        "version": info.version.text(),
        "caps": info.caps,
        "exeNames": [info.exe_names[i].text() for i in range(min(info.exe_count, 16))],
    }


def cross_check(manifest: dict, info: dict) -> list:
    errors = []
    if info["abiMajor"] != ABI_MAJOR:
        errors.append(f"get_info 的 ABI 主版本是 {info['abiMajor']}，应为 {ABI_MAJOR}")
    if info["id"] != manifest["id"]:
        errors.append(f"get_info 的 id（{info['id']}）与 plugin.json（{manifest['id']}）不一致")
    if info["version"] != manifest["version"]:
        errors.append(f"get_info 的版本（{info['version']}）与 plugin.json（{manifest['version']}）不一致")
    if sorted(n.lower() for n in info["exeNames"]) != sorted(n.lower() for n in manifest["exeNames"]):
        errors.append(f"get_info 的 EXE 列表 {info['exeNames']} 与 plugin.json {manifest['exeNames']} 不一致")
    expected = sum(CAP_BITS[c] for c in set(manifest.get("caps", [])))
    if info["caps"] != expected:
        errors.append(f"get_info 的能力位（{info['caps']}）与 plugin.json 的 caps（{expected}）不一致")
    return errors


def run(command: list, cwd: Path) -> None:
    print("+ " + " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], cwd=cwd, check=True)


def build(repo: Path, build_dir: Path, cmake_args: str, run_tests: bool) -> None:
    run(["cmake", "-S", repo, "-B", build_dir, "-A", "x64", *shlex.split(cmake_args, posix=False)], repo)
    run(["cmake", "--build", build_dir, "--config", "Release"], repo)
    if run_tests:
        # 没有注册测试时 ctest 返回 0
        run(["ctest", "--test-dir", build_dir, "-C", "Release", "--output-on-failure"], repo)


def find_module(build_dir: Path) -> Path:
    found = sorted(p for p in build_dir.rglob(MODULE_NAME) if "Release" in p.parts)
    if len(found) != 1:
        listing = ", ".join(str(p) for p in found) or "没有找到"
        raise ManifestError(f"Release 构建应当产出唯一一个 {MODULE_NAME}: {listing}")
    return found[0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_text(source: Path, target: Path) -> None:
    # 统一成 LF：plugin.json 的哈希是用户在主程序里确认的值，不能随 git 的 core.autocrlf 变化
    target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))


def package(repo: Path, module: Path, manifest: dict, out: Path) -> dict:
    plugin_id, version = manifest["id"], manifest["version"]
    folder = out / plugin_id
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    shutil.copyfile(module, folder / MODULE_NAME)
    copy_text(repo / MANIFEST_NAME, folder / MANIFEST_NAME)
    assets = list(dict.fromkeys(asset_names(manifest) + [n for n in CONVENTIONAL_ICONS if (repo / n).is_file()]))
    for name in assets:
        shutil.copyfile(repo / name, folder / name)
    for name in TEXT_FILES:
        if (repo / name).is_file():
            copy_text(repo / name, folder / name)

    zip_path = out / f"{plugin_id}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            archive.write(path, f"{plugin_id}/{path.name}")

    lines = [f"{sha256(p)}  {plugin_id}/{p.name}" for p in sorted(folder.iterdir())]
    lines.append(f"{sha256(zip_path)}  {zip_path.name}")
    sums = out / f"{plugin_id}-{version}.sha256.txt"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return {
        "id": plugin_id,
        "version": version,
        "zip": str(zip_path),
        "sums": str(sums),
        "dll_sha256": sha256(folder / MODULE_NAME),
        "manifest_sha256": sha256(folder / MANIFEST_NAME),
        "zip_sha256": sha256(zip_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--cmake-args", default="")
    parser.add_argument("--skip-build", action="store_true", help="只打包已经构建好的产物")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = (args.out or repo / "dist").resolve()
    build_dir = (args.build_dir or repo / "build").resolve()

    try:
        manifest = load_manifest(repo / MANIFEST_NAME)
        errors = validate_manifest(manifest)
        errors += [f"plugin.json 引用的文件不存在: {n}" for n in asset_names(manifest) if not errors and not (repo / n).is_file()]
        if not errors:
            if not args.skip_build:
                build(repo, build_dir, args.cmake_args, not args.skip_tests)
            module = find_module(build_dir)
            errors = cross_check(manifest, probe_module(module))
        if errors:
            for message in errors:
                print(f"::error::{message}")
            return 1
        result = package(repo, module, manifest, out)
    except (ManifestError, OSError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}")
        return 1

    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={value}\n")
    print(f"Package: {result['zip']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
