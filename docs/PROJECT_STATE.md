# NEBUMTK Offline 1.1 — Project State

Compact authoritative handoff. Replace stale facts rather than appending a log.
Last reconciled: 2026-11-04 (open-source conversion of baseline 1.1).

## Status

`baseline/1.1`（tag `v1.1.0`）是**最后实际发货的离线版**，已按开源要求转换：去掉访问
密码门禁、去掉 VMProtect 壳、去掉在线通道。产品行为与设备侧协议未改。

它是 2.0（`main`）的前身：`main` 是重构后的 typed-contract 版本。两条线是孤儿分支，
见 `docs/BASELINES.md`。

## Licensing

代码以 **AGPL-3.0-only** 发布（`LICENSE` 为 FSF/SPDX 原文）；第三方组件许可与"Qt 只能
动态链接"的约束见 `NOTICE.md`。

## What this is

一个纯本地的 Windows 产品：MediaTek v6 bootloader 解锁 + 分区备份/还原 + 品牌流程，
单 Python/PySide6 应用（`gui/toolbox_ui.py` 内联五页 UI + `gui/worker.py` 单 worker），
Nuitka standalone 分发。无服务端、无账号、无网络、无访问密码。

## Architecture

```text
nebumtk_unlock_tools.__main__ -> app.main()
  -> gui.main_window.MainWindow
  -> gui.worker.FlowWorker
  -> flows.factory.create_flow()
  -> FlowContext + AdbClient/FastbootClient + ResourceLoader + OperationFiles
```

- 启动不再有密码提示；`app.main()` 直接构造 `MainWindow`。
- `keymaterial`（新）= 素材查找层：显式 root → `NUT_MATERIAL_ROOT` → `<项目>/material`。
- `resources/payload_store`（新）= preload 库；`resources/artifact_store`（新）=
  fastboot artifact 库（Vivo wipe misc、reserve LK 等）；两者都读本地素材，取代
  `embedded_preloads` / `embedded_fastboot_artifacts` / `embedded_sakisu` 密文模块。
- `lk/keys.py` 直接读 `material/xingc/*` 明文，取代 `lk/embedded_xingc.py`。
- `build_profile` 恒为 `offline`；`is_online()` 恒为 False，
  `nebumtk_unlock_tools.online` 已删除。

## Product boundaries

- 持久 Root 管理不在范围内。例外是 SakiSU 4.3 的 `init_boot` 镜像补丁与可选的 Vivo
  原槽写入。
- 临时提权（受保护 preload + 一次性 `su`）是备份与 system 侧分区写入的内部前提。
- 测试模式只放松兼容性匹配；精确设备、传输状态、槽位、容量、冻结哈希、取消与终局
  复验永不放松。
- 仓库不携带厂商素材（LK、payload、Xingc 私钥、固件），也不携带任何密码或壳。

## Verified state (2026-11-04)

```text
python -m pytest -q  ->  345 passed, 13 skipped      (nut-oss baseline/1.1)
```

13 个 skip 全部是"操作者素材缺失"（payload `.so`、`xingc_sources/`、artifact store）。

本轮同时修掉一个真实缺陷：测试曾经读操作者机器上的
`%APPDATA%/NEBUMTK/settings.json`，本机若开着测试模式，`MainWindow` 会在
`_confirm_test_mode_operation` 弹模态框，把整个测试进程挂住。现在 `SettingsStore`
支持 `NEBUMTK_SETTINGS_DIR`，`tests/conftest.py` 把设置目录与 material root 都指到
临时目录。

未执行：Nuitka 构建与发行审计、SFX 打包、真机端到端验收。

## Release pipeline (per candidate)

```powershell
python -m pytest -q tests --basetemp=<project-local-temp>
.\scripts\clean.ps1
.\scripts\build.ps1 -Channel Offline
python tools\audit_distribution.py dist\nebumtk_unlock_tools.dist
.\scripts\package-sfx.ps1        # 可选
```

构建链里没有壳阶段。旧 `dist/`、旧哈希或过去的 smoke 结果不代表当前源码已发布。

## Known risks

- 2026-07 泄漏事件按完全公开处理：闭源树里的密钥与 payload 材料不能再当秘密。
- 1.1 的 UI 是单文件大布局（`main_window.py` ~86 KB）；不要再往里堆功能，2.0 已把它
  拆成 `gui/pages/*`。
- `misc/` 里的品牌 bootloader 镜像随仓库分发（厂商内容，见 `NOTICE.md`）。
- SakiSU 是上游项目（<https://github.com/XingChenRS/SakiSU>），二进制不进仓库；
  `tools/fetch_sakisu.py` 把 `v4.3.0-sakisu.1` 拉进 `material/sakisu/`。

## Next decisions

- 1.1 是否只作只读参考；共享的安全修复是否两分支同步。
- 真机验收排期。
