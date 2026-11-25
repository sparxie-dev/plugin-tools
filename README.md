# plugin-tools

[Sparxie](https://github.com/sparxie-dev/Sparxie) 第三方游戏插件的校验、构建打包脚本，以及供插件仓库调用的可复用工作流。

插件作者**不需要复制本仓库的任何文件**。从 [plugin-template](https://github.com/sparxie-dev/plugin-template) 创建的仓库，其 `validate.yml` 和 `release.yml` 直接调用这里的工作流。

## 插件仓库要满足的约定

- 根目录有 CMake 工程和 `plugin.json`（带 `version`）。
- Release 构建产出**唯一一个** `module.dll`（放在任意输出目录下的 `Release` 里）。
- 想跑的原生测试用 `add_test` 注册，工作流会执行 `ctest`。
- 放在根目录的 `NOTICE.md`、`README.md`、`LICENSE` 会打进包；`plugin.json` 里声明的 `icon` / `background` 也会打进包，根目录有 `icon.ico` / `icon.png` 时一并带上。

## 工作流

| 文件 | 作用 |
|---|---|
| `reusable-validate.yml` | push / PR：校验 `plugin.json`，构建、测试、打包，并校验打出来的包 |
| `reusable-release.yml` | 推 `v*` tag：同样的步骤，另外要求 tag 等于 `v<version>`，生成带哈希的发布说明并创建 GitHub Release（zip 和哈希清单） |

```yaml
uses: sparxie-dev/plugin-tools/.github/workflows/reusable-release.yml@v1
```

输入参数都是可选的：`runs-on`（默认 `windows-latest`）、`cmake-args`、`python-version`。

构建之后会**加载刚编出来的 `module.dll`**，调用 `get_info`，和 `plugin.json` 核对 id、版本、EXE 列表和能力位——主程序加载插件时做同样的核对（版本这一项是发布规则，主程序不要求）。只调用 `get_info`，不创建会话，不启动任何程序。

**已经存在同名 Release 时直接失败。** `module.dll` 每次编译哈希都不同，覆盖已发布的附件会让用户核对过的哈希失效，要改版本号重发。版本号带 `-`（如 `1.2.0-rc.1`）的会标成预发布。

插件仓库放一个 `.github/release-notes.template.md` 就能替换默认的发布说明模板，可用占位符：`{{DISPLAY_NAME}}` `{{VERSION}}` `{{PLUGIN_ID}}` `{{ZIP_NAME}}` `{{DLL_SHA256}}` `{{MANIFEST_SHA256}}` `{{ZIP_SHA256}}` `{{RISKS}}` `{{CHANGES}}`。

## 本地验证（可选）

在插件仓库根目录执行，版本与 CI 的 `@v1` 一致（需要 Python 3.11+、CMake 和 Visual Studio）：

```powershell
git clone --depth 1 --branch v1 https://github.com/sparxie-dev/plugin-tools.git .plugin-tools
python .plugin-tools/scripts/validate_plugin.py --repo .
python .plugin-tools/scripts/build_plugin.py --repo .
```

把 `.plugin-tools/`、`build/`、`dist/` 加进插件仓库的 `.gitignore`。

## 维护约定

- `v1` 是浮动 tag，指向最新的 `v1.x.y`；`v1` 系列只做向后兼容的修复，破坏性改动发 `v2`。想完全锁定的调用方可以换成提交 sha。
- 本仓库对 `v*.*.*` tag 开启保护（禁止删除和修改），浮动的 `v1` 不受限，发修复版本时移动它。工作流保持 `persist-credentials: false`，并按 `job.workflow_sha` 检出自身，保证脚本和工作流是同一个提交。
- `scripts/manifest_utils.py` 的校验规则对齐 Sparxie 主程序的 `PluginManifest` / `PluginSettingsSchema`。**主程序是最终裁判**：主程序改规则时要同步改这里。
- `tests/sample_plugin_repo` 是回归用的最小插件，CI 会完整构建打包它。
