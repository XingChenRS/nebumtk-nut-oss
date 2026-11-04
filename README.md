# NEBUMTK Unlock Tools 1.1 — open source

MediaTek v6 bootloader unlock and local partition work for Windows, as a single
Python/PySide6 desktop application. Fully local: no server, no account, no
network, no access password.

This branch is **baseline 1.1** — the last offline build that actually shipped.
`main` carries the refactored 2.0 baseline. Both are orphan branches of the same
repository, so they diff directly:

```powershell
git diff baseline/1.1..main -- nebumtk_unlock_tools
git worktree add ..\nut-2.0 main
```

See [docs/BASELINES.md](docs/BASELINES.md) for what actually differs.

## 与闭源发行版的差异

开源处理只删三类东西，产品行为不变：

1. **访问密码门禁**（`security.py`）：闭源版用 PBKDF2 从访问密码派生 master key，
   再解内置的 preload 密文、Xingc 密钥、fastboot artifact 与 SakiSU patcher。开源版
   没有门禁，也没有随仓库分发的密文——材料改成操作者本地文件
   （见 [docs/MATERIAL.md](docs/MATERIAL.md)），缺失时报 `MaterialError` 并给出路径。
2. **VMProtect 壳**：`vmp/`、`scripts/protect-*.ps1`、`tools/*vmp*` 与 SFX 压缩密码
   全部移除。发行审计仍拒绝 `.vmp`/`.map`/`.pdb`/`*.vmp.exe` 进发行目录（卫生规则，
   不是构建依赖）。
3. **在线通道**：`nebumtk_unlock_tools/online/`、`gui/login_dialog.py`、在线构建与审计
   脚本删除；`build_profile` 只剩 `offline`，`is_online()` 恒为 False。

设备侧协议（加密 wrapper、X25519 challenge、HKDF-SHA256 + AES-GCM grant、匿名 fd
执行）未改。

## 代码结构

```text
nebumtk_unlock_tools.__main__ -> app.main()
  -> gui.main_window.MainWindow       (五页 UI，布局在 gui/toolbox_ui.py)
  -> gui.worker.FlowWorker
  -> flows.factory.create_flow()
  -> FlowContext + AdbClient/FastbootClient + ResourceLoader + OperationFiles
```

- `nebumtk_unlock_tools/` 是产品，`tests/` 是可执行契约（26 个测试文件）。
- `keymaterial.py`（素材路径解析）、`resources/payload_store.py`（preload 库）、
  `resources/artifact_store.py`（fastboot artifact 库）是本轮新增的替换层。
- `native/preload_wrapper/` 是设备侧 C wrapper 源码（第三方组件见 [NOTICE.md](NOTICE.md)）。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pytest==9.0.2
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-basetemp
.\.venv\Scripts\python.exe -m nebumtk_unlock_tools
```

依赖操作者素材的用例（payload `.so`、`xingc_sources/`、artifact store）缺素材时
skip，不失败。

## 素材要求

仓库没有秘密，也没有可直接上设备的 payload。要跑品牌流程需要自己准备 `material/`
（preload store、artifact store、Xingc 密钥、SakiSU patcher）、`preloads/`（明文
payload 源）、`xingc_sources/`。布局见 [docs/MATERIAL.md](docs/MATERIAL.md)。

## 构建

```powershell
.\scripts\build.ps1 -Channel Offline
python tools\audit_distribution.py dist\nebumtk_unlock_tools.dist
.\scripts\package-sfx.ps1        # 可选
```

## 上游项目：SakiSU

SakiSU 是另一个独立项目：<https://github.com/XingChenRS/SakiSU>（引用
`v4.3.0-sakisu.1`）。本仓库**不分发它的二进制**，只按 SHA-256 校验后调用；产物由
操作者拉取到 `material/sakisu/`：

```powershell
python tools\fetch_sakisu.py --download          # 官方 APK -> material/sakisu
python tools\fetch_sakisu.py --apk <apk> --host <ksud.exe>
python tools\fetch_sakisu.py --check
```

## 署名与溯源

```
Copyright (c) 2026 雾月星辰 & MLXC <github@XingChenRS>
SPDX-License-Identifier: AGPL-3.0-only
Provenance token: NBMTK-canary-7f3a91c4e8b2d05f
```

每个第一方源文件带这两行 SPDX 头；`LICENSE` 开头是同一份署名；`--license` 会把它打印
出来。删掉这些声明的发行版不是合法副本（AGPL §5），抄走的代码里仍会带着 token——
`python tools\audit_provenance.py --scan <path>` 可以把证据列出来。详见
[docs/PROVENANCE.md](docs/PROVENANCE.md)。

## 许可

本项目代码以 **GNU Affero General Public License v3.0（only）** 发布——强著佐权，不是
宽松许可。全文见 [LICENSE](LICENSE)。

- 分发编译产物必须同时提供完整对应源码，衍生作品只能继续 AGPL-3.0-only。
- 包装成在线服务同样触发 AGPL §13：要向服务对象提供源码。
- 不能用 MIT/Apache 之类条款重新发布本项目代码。
- 第三方组件（Monocypher、PyCryptodome、Qt/PySide6、Android platform-tools、厂商
  misc 镜像、上游 SakiSU）按各自许可，见 [NOTICE.md](NOTICE.md)；Qt/PySide6 是
  LGPL-3，只做动态链接。
