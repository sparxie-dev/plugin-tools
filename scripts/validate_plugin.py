"""校验插件仓库或打好的插件包。

  python validate_plugin.py --repo .                              # 校验仓库根的 plugin.json 和它引用的文件
  python validate_plugin.py --repo . --expect-tag v1.2.3          # 同时要求版本与 tag 一致
  python validate_plugin.py --zip dist/x.zip [--expect-id ID] [--expect-tag v1.2.3]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

from manifest_utils import (MANIFEST_NAME, MAX_IMAGE_BYTES, MODULE_NAME, ManifestError, asset_names,
                            load_manifest, validate_manifest)


def check_folder(folder: Path, errors: list) -> dict:
    data = load_manifest(folder / MANIFEST_NAME)
    errors.extend(validate_manifest(data))
    for name in asset_names(data) if not errors else []:
        path = folder / name
        if not path.is_file():
            errors.append(f"plugin.json 引用的文件不存在: {name}")
        elif not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            errors.append(f"图片为空或超过 8 MB: {name}")
    return data


def check_expectations(data: dict, errors: list, expect_id: str | None, expect_tag: str | None) -> None:
    if expect_id and data.get("id") != expect_id:
        errors.append(f"id 应为 {expect_id}，实际是 {data.get('id')}")
    if expect_tag and expect_tag != f"v{data.get('version')}":
        errors.append(f"tag {expect_tag} 与 plugin.json 的版本 {data.get('version')} 不一致（tag 必须是 v<version>）")


def check_zip(path: Path, errors: list) -> dict:
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as temp:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        if len(tops) != 1 or any("/" not in n for n in names):
            raise ManifestError("zip 里应当只有一个以插件 id 命名的顶层目录")
        if any(n.startswith("/") or ".." in n.split("/") for n in names):
            raise ManifestError("zip 含越界路径")
        archive.extractall(temp)
        top = next(iter(tops))
        folder = Path(temp) / top
        data = check_folder(folder, errors)
        if data.get("id") != top:
            errors.append(f"顶层目录名 {top} 必须等于插件 id {data.get('id')}")
        if not (folder / MODULE_NAME).is_file():
            errors.append(f"包里缺少 {MODULE_NAME}")
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", type=Path)
    group.add_argument("--zip", type=Path)
    parser.add_argument("--expect-id")
    parser.add_argument("--expect-tag")
    args = parser.parse_args()

    errors: list = []
    try:
        data = check_zip(args.zip, errors) if args.zip else check_folder(args.repo, errors)
        check_expectations(data, errors, args.expect_id, args.expect_tag)
    except (ManifestError, OSError, zipfile.BadZipFile) as error:
        errors.append(str(error))
    if errors:
        for message in errors:
            print(f"::error::{message}")
        return 1
    print(f"OK: {data['id']} {data['version']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
