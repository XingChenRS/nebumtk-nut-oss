# NEBUMTK Unlock Tools — open source

MediaTek v6 bootloader unlock and local partition work for Windows, as a single
Python/PySide6 desktop application. Fully local: no server, no account, no
network, no access password.

This repository is the **open-source release** of the NUT tool. It carries two
baselines of the same product:

| baseline | branch | tag | 形态 |
|---|---|---|---|
| 1.1 | `baseline/1.1` | `v1.1.0` | 最后实际发货的离线版（五页 UI 的第一版） |
| 2.0 | `main` | `v2.0.0` | 重构后的离线版（typed contracts、SakiSU 主机侧补丁边界、通用分区备份） |

两个 baseline 是同一仓库的两个孤儿分支，可以直接互相 diff：

```powershell
git log --oneline baseline/1.1
git diff baseline/1.1..main -- nebumtk_unlock_tools
# 或者并排工作树
git worktree add ..\nut-1.1 baseline/1.1
```

## 与闭源发行版的差异

开源处理只删两类东西，产品行为不变：

1. **访问密码门禁**（`security.py`）：闭源版用 PBKDF2 从操作者输入的访问密码派生
   master key，再用它解内置的 preload 密文、Xingc 密钥与机型包密钥。开源版没有这个
   门禁，也没有任何随仓库分发的密文——受保护材料改成操作者自己的本地文件
   （见 [docs/MATERIAL.md](docs/MATERIAL.md)）。缺文件时报 `MaterialError` 并给出
   绝对路径，不再表现为"密码错了"。
2. **VMProtect 壳**（`vmp/`、`scripts/protect-*.ps1`、`tools/*vmp*`）：开源版不带
   壳、不带 receipt 校验，SFX 也不再加压缩密码。`tools/audit_distribution.py` 仍然
   拒绝 `.vmp`/`.map`/`.pdb`/`*.vmp.exe` 这类中间物进发行目录——那是卫生规则，不是
   构建依赖。

设备侧协议（加密 wrapper、X25519 challenge、HKDF-SHA256 + AES-GCM grant、匿名 fd
执行）**一字未改**，所以行为与 1.1/2.0 完全一致。

## 代码结构

```text
nebumtk_unlock_tools.__main__ -> app.main()
  -> gui.main_window.MainWindow      (五页：设备 / 分区与备份 / Root 与解锁 / 镜像与恢复 / 工具与设置)
  -> QThread workers
  -> flows.factory.create_flow()     (flow_kind -> typed flow)
  -> FlowContext + AdbClient/FastbootClient + ResourceLoader + OperationFiles
```

- `nebumtk_unlock_tools/` 是产品本体，`tests/` 是可执行契约。
- `core/operation_files`（不可变输入与操作私有输出）、`core/safety`（终局复验）、
  `core/slot`（活动/非活动槽配对）、`resources/loader`（preload 物化）、
  `lk/ubl_service` + `lk/xingc_service`（LK 补丁与 Xingc 授权）是关键契约层。
- `native/preload_wrapper/` 是设备侧 C wrapper 源码（monocypher + pycryptodome 的
  AES-GCM/SHA-256 实现，见 [NOTICE.md](NOTICE.md)）。
- `tools/` 是构建与校验工具，`scripts/` 是构建与打包入口。

## 快速开始（开发机）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pytest==9.0.2
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-basetemp
.\.venv\Scripts\python.exe -m nebumtk_unlock_tools
```

测试里有 19 个用例依赖操作者私有素材（payload `.so`、Xingc 目录、审阅过的静态
构建），缺素材时 skip 而不是失败——这是刻意的，仓库不携带这些素材。

## 素材要求

仓库里**没有任何秘密**，也没有可直接上设备的 payload。要真正跑品牌流程，需要自己
准备 `material/`（密钥与 payload store）、`preloads/`（明文 payload 源）、
`xingc_sources/`（Xingc 密钥与补丁目录）。目录布局与生成命令见
[docs/MATERIAL.md](docs/MATERIAL.md)。

不带素材时程序照常启动，设备扫描与分区浏览可用，只有需要 payload 的解锁/提权流程会
以 `MaterialError` 明确失败。

## 构建

```powershell
.\scripts\build.ps1 -Channel Offline      # Nuitka standalone -> dist/nebumtk_unlock_tools.dist
python tools\audit_distribution.py dist\nebumtk_unlock_tools.dist
.\scripts\package-sfx.ps1                 # 可选：打成自解压 exe
```

`scripts/build.ps1` 在 `material/payloads/manifest.json` 存在时会顺带校验 payload
store；没有 store 就跳过并给出黄色提示。

## 边界

- 本仓库不含 LK 镜像、机型固件、厂商 bootloader 补丁或随包 payload；这些都由操作者
  在运行时选择或自行准备。
- 不提供持久 Root 管理（无 KernelSU/SakiSU/Magisk 管理器、无 `libksud.so`、无 root
  控制台）。唯一例外是 `init_boot` 的 SakiSU 镜像补丁边界和可选的 Vivo 原槽写入。
- 分发目录审计禁止明文 payload、Xingc 私密材料、调试符号与壳中间物。
- 工具只应作用于你自己拥有或已获授权的设备。

## 上游项目：SakiSU

SakiSU 是另一个独立项目：<https://github.com/XingChenRS/SakiSU>（引用
`v4.3.0-sakisu.1`）。本仓库**不再分发它的二进制**，只保留自己的哈希清单
`sakisu/*manifest.json` 作为索引，产物由操作者拉取：

```powershell
python tools\fetch_sakisu.py --download          # 官方 APK -> material/sakisu
python tools\fetch_sakisu.py --apk <apk> --host <ksud.exe>
python tools\fetch_sakisu.py --check
```

## 许可

代码以 MIT 发布，见 [LICENSE](LICENSE)。第三方组件与不随仓库分发的厂商素材另见
[NOTICE.md](NOTICE.md)。
