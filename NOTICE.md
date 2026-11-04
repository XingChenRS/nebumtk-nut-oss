# 第三方组件与不随仓库分发的素材

本仓库的代码以 MIT 发布（见 [LICENSE](LICENSE)）。下列内容不属于本项目代码，各自
遵循其原始许可。

## 随仓库分发的第三方组件

| 路径 | 组件 | 许可 |
|---|---|---|
| `native/preload_wrapper/vendor/monocypher/` | Monocypher（X25519 / XChaCha20-Poly1305 / BLAKE2b） | BSD-2-Clause / CC0-1.0（双许可，见该目录 `LICENCE.md`） |
| `native/preload_wrapper/vendor/pycryptodome/src/` | PyCryptodome 的 AES / GHASH / SHA-256 单文件实现 | Public Domain（见该目录 `README.md` 与 `licenses/`） |
| `platform-tools/` | Android SDK Platform Tools（adb / fastboot 等） | Android SDK 条款，随附 `NOTICE.txt` 与 `source.properties` |
| `misc/` | 各品牌 `misc` 分区 bootloader 镜像片段 | 厂商固件内容，不属于本项目；仅用于对应品牌流程 |

## 上游项目：SakiSU（只做索引，不分发）

SakiSU 是**另一个独立项目**：<https://github.com/XingChenRS/SakiSU>，本项目引用的是
它的 `v4.3.0-sakisu.1` 发布。

- 本仓库**不包含** SakiSU 的任何二进制。`sakisu/` 里只保留我们自己的哈希固定清单
  （`official-patcher-manifest.json`、`official-patcher-windows-x64-manifest.json`），
  用来把上游产物绑到确切的 SHA-256 与接口上。
- 产物由操作者用 `python tools/fetch_sakisu.py` 拉取到 `material/sakisu/`
  （该目录在 `.gitignore` 里），运行时按清单校验后才使用。
- 许可与再分发条件以 SakiSU 上游为准；本项目只调用它，不再分发它。

## 不随仓库分发、由操作者自行准备的素材

| 路径 | 内容 | 说明 |
|---|---|---|
| `preloads/<payload_id>/preload.so` | 明文提权 payload（arm64 ELF） | 用 `tools/embed_preloads.py --regenerate` 编译成加密 wrapper |
| `material/` | preload store、解封私钥、Xingc 密钥与补丁目录、机型包密钥、SakiSU 上游产物 | 见 [docs/MATERIAL.md](docs/MATERIAL.md) |
| `xingc_sources/` | Xingc 私钥/公钥 blob 与补丁目录 JSON | 用 `tools/import_xingc_material.py` 导入 `material/xingc/` |
| `lk_sources/`、`models/`、`local-dev/`、`.private/` | 厂商 LK 镜像、机型源材料、构建签名密钥、审阅过的静态构建产物 | 均为本机专用输入 |

这些目录都在 `.gitignore` 里。仓库不携带它们，是为了让"开源发布"不变成"泄漏发布"。
