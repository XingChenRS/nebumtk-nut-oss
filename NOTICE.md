# 许可与第三方组件

## 1. 本项目的许可：AGPL-3.0-only

本项目代码（`nebumtk_unlock_tools/`、`tools/`、`tests/`、`scripts/`、`docs/`，以及
`native/preload_wrapper/` 中我们自己写的部分）以 **GNU Affero General Public License
version 3.0 only** 授权。全文见 [LICENSE](LICENSE)。

`only` 是刻意的：不附加"或任何更高版本"。

```
Copyright (c) 2026 雾月星辰 & MLXC <github@XingChenRS>
SPDX-License-Identifier: AGPL-3.0-only
Provenance token: NBMTK-canary-7f3a91c4e8b2d05f
```

`LICENSE` 保持 AGPL 原文（便于许可扫描器识别），署名正文在 `COPYRIGHT`。
逐文件声明、token 与取证流程见 [docs/PROVENANCE.md](docs/PROVENANCE.md)。

分发或提供网络服务时必须满足 AGPL §4–§6 与 §13：随二进制提供完整对应源码（或书面
要约），并以同一许可授权衍生作品；把它包成在线服务同样要提供源码。

## 2. 与 AGPL 共存的第三方组件（各自许可，不被本项目重新授权）

| 路径 | 组件 | 许可 | 与 AGPL 的关系 |
|---|---|---|---|
| `nebumtk_unlock_tools/lk/vendor/` | libLK（LK 镜像解析与证书处理），© 2025-2026 Roger Ortiz | **GPL-3.0-or-later**（文件头已带 SPDX，保持原样） | AGPL §13 明文允许与 GPLv3 作品合并分发；GPL 部分继续按 GPL 授权，合并作品的网络条款按 AGPL 执行。**不能把它改成 AGPL 头** |
| `native/preload_wrapper/vendor/monocypher/` | Monocypher（X25519 / XChaCha20-Poly1305 / BLAKE2b） | BSD-2-Clause / CC0-1.0 双许可 | 宽松许可，可并入 AGPL 作品；保留其版权与许可声明 |
| `native/preload_wrapper/vendor/pycryptodome/src/` | PyCryptodome 的 AES / GHASH / SHA-256 单文件实现 | Public Domain | 无冲突 |
| `platform-tools/` | Android SDK Platform Tools（adb / fastboot 等） | Android SDK 条款（随附 `NOTICE.txt`、`source.properties`） | **独立聚合**：放进本仓库不改变其许可，再分发需遵守 SDK 条款 |
| `misc/` | 各品牌 `misc` 分区 bootloader 镜像片段 | 厂商固件内容，不属于本项目 | 同上：聚合分发，本项目的 AGPL 不覆盖它们 |
| `sakisu/*manifest.json` | 我们自己的哈希固定清单 | AGPL-3.0-only | 属于本项目代码 |

### 运行期依赖（不随仓库分发）

| 组件 | 许可 | 约束 |
|---|---|---|
| PySide6 / Qt 6 | LGPL-3.0（Qt 另有商业许可） | 只允许**动态链接**；分发时保留 Qt 许可文本与替换库的能力；不得静态链接 Qt |
| PyYAML、cryptography、pyusb、libusb、pyasn1 | MIT / Apache-2.0 / BSD / LGPL-2.1 等 | 可与 AGPL 作品共存；按各自条款保留声明 |

## 3. 上游项目：SakiSU（只做索引，不分发）

SakiSU 是**另一个独立项目**：<https://github.com/XingChenRS/SakiSU>，本项目引用其
`v4.3.0-sakisu.1` 发布。

- 本仓库**不包含** SakiSU 的任何二进制；`sakisu/` 只留我们自己的哈希固定清单。
- 产物由操作者用 `python tools/fetch_sakisu.py` 拉取到 `material/sakisu/`
  （`.gitignore` 内），运行时按清单校验后才使用。
- 它的许可与再分发条件以 SakiSU 上游为准；本项目的 AGPL 不覆盖它，也不改变它。

## 4. 不随仓库分发、由操作者自行准备的素材

| 路径 | 内容 | 说明 |
|---|---|---|
| `preloads/<payload_id>/preload.so` | 明文提权 payload（arm64 ELF） | 用 `tools/embed_preloads.py --regenerate` 编译成加密 wrapper |
| `material/` | preload store、解封私钥、Xingc 密钥与补丁目录、机型包密钥、SakiSU 上游产物 | 见 [docs/MATERIAL.md](docs/MATERIAL.md) |
| `xingc_sources/` | Xingc 私钥/公钥 blob 与补丁目录 JSON | 用 `tools/import_xingc_material.py` 导入 `material/xingc/` |
| `lk_sources/`、`models/`、`local-dev/`、`.private/` | 厂商 LK 镜像、机型源材料、构建签名密钥、审阅过的静态构建产物 | 均为本机专用输入 |

这些目录都在 `.gitignore` 里。仓库不携带它们，是为了让"开源发布"不变成"泄漏发布"。

## 5. 贡献

提交即表示你同意以 AGPL-3.0-only 授权你的贡献（inbound = outbound）。新增文件首行带
`# SPDX-License-Identifier: AGPL-3.0-only`。
