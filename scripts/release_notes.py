"""用打好的包和 git 历史填写发布说明模板。

  python release_notes.py --repo . --dist dist --tag v1.2.3 [--output dist/RELEASE_NOTES.md] [--template path]

插件仓库根目录有 .github/release-notes.template.md 时优先用它，否则用本仓库的默认模板。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from build_plugin import sha256
from manifest_utils import MANIFEST_NAME, MODULE_NAME, load_manifest

DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "release-notes.md"
CAP_WARNINGS = {
    "injects": "会向游戏进程注入代码，可能违反游戏用户协议并有被封号的风险。",
    "createsProcess": "会自己创建游戏进程。",
}


def git(repo: Path, *args: str) -> str | None:
    done = subprocess.run(["git", "-C", str(repo), "-c", "i18n.logOutputEncoding=UTF-8", *args],
                          capture_output=True, encoding="utf-8", errors="replace")
    return done.stdout.strip() if done.returncode == 0 else None


def changes_since_previous_tag(repo: Path, tag: str) -> str:
    previous = git(repo, "describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^")
    log = git(repo, "log", f"{previous}..{tag}" if previous else tag, "--no-merges", "--pretty=format:%s")
    subjects = [line for line in (log or "").splitlines() if line]
    return "\n".join(f"- {line}" for line in subjects) or "- （没有记录到改动）"


def risk_text(manifest: dict) -> str:
    lines = ["第三方插件，没有沙箱，以管理员权限运行原生代码。请只启用你信任来源的版本。"]
    lines += [text for cap, text in CAP_WARNINGS.items() if cap in manifest.get("caps", [])]
    if manifest.get("registryHooks"):
        lines.append("会修改声明范围内的当前用户注册表项（Sparxie 备份并在结束后恢复）。")
    return "\n".join(f"- {line}" for line in lines)


def render(repo: Path, dist: Path, tag: str, template: Path) -> str:
    manifest = load_manifest(repo / MANIFEST_NAME)
    folder = dist / manifest["id"]
    zip_path = dist / f"{manifest['id']}-{manifest['version']}.zip"
    values = {
        "DISPLAY_NAME": manifest["displayName"],
        "VERSION": manifest["version"],
        "PLUGIN_ID": manifest["id"],
        "ZIP_NAME": zip_path.name,
        "DLL_SHA256": sha256(folder / MODULE_NAME),
        "MANIFEST_SHA256": sha256(folder / MANIFEST_NAME),
        "ZIP_SHA256": sha256(zip_path),
        "RISKS": risk_text(manifest),
        "CHANGES": changes_since_previous_tag(repo, tag),
    }
    text = template.read_text(encoding="utf-8")
    unknown = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", text)) - values.keys()
    if unknown:
        raise SystemExit(f"模板里有未知占位符: {', '.join(sorted(unknown))}")
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: values[m.group(1)], text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    dist = (args.dist or repo / "dist").resolve()
    local = repo / ".github" / "release-notes.template.md"
    template = args.template or (local if local.is_file() else DEFAULT_TEMPLATE)
    output = args.output or dist / "RELEASE_NOTES.md"
    output.write_text(render(repo, dist, args.tag, template), encoding="utf-8", newline="\n")
    print(f"Release notes: {output}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
