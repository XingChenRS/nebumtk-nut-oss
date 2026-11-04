# NEBUMTK 维护手册

本手册只记录当前仍生效的维护契约。源码、测试和 `AGENTS.md` 的约束高于本文；
`docs/superpowers/` 是历史资料，不能当作当前需求。

## 1. 当前产品边界

- 活跃源码只有 `relay/nebumtk/`，不要混用同级旧工程或既有 `dist/`。
- 当前交付目标是离线版。在线客户端和服务端保留在仓库中，但处于冻结状态；除非明确恢复，
  不得把在线假设带入离线构建，也不得声称在线版可交付。
- 在线版和离线版是两个物理构建，不能靠运行时开关互相切换。
- 持久 Root 管理已移出产品。发行包、GUI 和 flow 不得重新加入 KernelSU、SakiSU、
  ReSuki、Magisk、KMI 选择、管理器 APK、`libksud.so`、boot 镜像修补/刷写或安装流程。
- 临时提权仍是备份和 system 侧分区操作的内部前置条件。它通过受保护 preload 和一次性
  `su` 通道完成，不代表安装持久 Root。
- `init_boot`、`vendor_boot` 仍可按机型配置作为原机备份项；它们不得被解释为待修补或待刷写资源。
- 投屏功能已经删除，发行包和文档不得重新捆绑 scrcpy 运行时。

## 2. 当前结构与入口

启动链：

```text
nebumtk_unlock_tools.__main__
  -> app.main()
  -> MainWindow（开源版没有访问密码，也没有在线登录）
  -> MainWindow
  -> 后台 worker
  -> flows.factory.create_flow()
  -> FlowContext + ADB/Fastboot/资源/操作文件
```

GUI 当前按行为拆成五个区域：设备概览、预备流程、Fastboot 分区、品牌功能和高级工具。
维护时不要把通用备份、写分区或 Fastboot 同步重新嵌回某个品牌页面。

当前 flow 路由以 `nebumtk_unlock_tools/flows/factory.py` 为准：

| flow kind | 模式 | 作用 |
|---|---|---|
| `root_escalation` | ADB | 仅执行本次操作所需的临时提权 |
| `backup_export` | ADB | 备份配置指定的原机分区并导出 manifest |
| `vivo_complete_unlock` | ADB / Fastbootd / Fastboot / USB | Vivo 组合流程，不安装持久 Root |
| `write_inactive` | ADB / Fastboot | 校验并写入非活动槽 LK |
| `patch_lk` | 本地 | 修补并验证 Vivo LK |
| `oppo_unlock` | bootloader Fastboot | 执行 OPPO unlock；已解锁时短路成功 |
| `restore_backup_lk` | bootloader Fastboot | 还原经 manifest 验证的本机原始 LK |
| `vivo_xingc_unlock` | bootloader Fastboot + USB | 执行 Xingc 授权透传 |
| `dual_slot_sync` | bootloader Fastboot | 将显式选中的 patched LK 同步到双槽 |

新增或改名 flow 时，至少同步检查 factory、worker 模式分类、GUI 输入冻结和
`tests/test_session_free_flows.py`、`tests/test_gui.py`、`tests/test_transports.py`。

## 3. 一次性临时提权

离线发行包只允许携带加密且带认证的内置 preload wrapper。执行边界如下：

1. 根据已匹配 profile 选择内置受保护资源；普通模式拒绝明文 `preload.so`。
2. 每次尝试都由设备生成新挑战，本机为该设备生成一次性 grant。
3. 禁止把可复用原始 CEK 通过 ADB 传入设备，也禁止生产 wrapper 接受旧式 64 位十六进制密钥。
4. 明文 payload 只允许在设备匿名 fd/inode 中短暂存在并立即执行；不得增加命名文件 fallback。
5. 成功、失败、超时或取消都清理远端 wrapper；需要重试时必须从新挑战和新 grant 重新开始。
6. profile 中的启动程序、参数、环境变量、执行模式和远端路径均属于机型契约，不得擅自归一化。

测试模式只允许放宽 profile、固件和 payload 兼容匹配，不能放宽设备唯一性、连接模式、槽位、
分区存在性/容量、操作文件哈希、取消或最终写入复核。测试模式造成的兼容风险由操作者承担，
但不能因此允许明文资源或绕过一次性授权。

主要实现：

- `nebumtk_unlock_tools/core/root.py`：挑战、grant、执行、重试和清理。
- `nebumtk_unlock_tools/resources/loader.py`：受保护 preload 解析。
- `nebumtk_unlock_tools/core/device.py`：机型与 privilege runtime 契约。
- `nebumtk_unlock_tools/flows/base.py`：flow 侧解析与临时提权入口。

## 4. 备份、操作文件与危险边界

- 外部输入必须先由 `OperationFiles.copy_input()` 复制到本次操作私有目录，验证大小、类型和
  SHA-256；危险动作前调用 `verify_unchanged()`。
- 备份导出必须原子写入 `backup-manifest.json`，记录 schema、profile、ADB serial、槽位以及
  每个分区的实际名称、文件名、大小和 SHA-256。
- 恢复 LK 时重新核对当前 Fastboot serial、profile、manifest 中的精确文件名、大小和哈希。
  旧备份只有在目录命名严格绑定设备序列号时才允许兼容，并必须显示警告。
- 创建桌面备份目录或 manifest 失败必须在设备写入前终止，不能把未导出的临时文件当成备份。
- 进入 `dd` 或 `fastboot flash` 前重新确认实时 serial、模式、槽位、分区、容量、输入哈希和取消状态。
- 外部手选 Vivo LK 必须先通过 patched LK 验证；自动流程使用已经冻结并验证的快照。
- 无有效零填充尾部的 raw LK，只允许通过结构验证的 cert2 override 小幅增长；不能把原文件长度
  当作真实分区容量。最终写入仍以设备实时分区容量为准。

## 5. 目录职责

| 路径 | 当前职责 |
|---|---|
| `nebumtk_unlock_tools/` | 客户端源码与运行逻辑事实来源 |
| `devices.yaml` | 维护端机型目录输入；发行时使用签名后的内嵌目录 |
| `preloads/` | 明文构建输入，不得进入发行包 |
| `xingc_sources/` | Xingc 构建输入，不得进入日志或发行包 |
| `native/preload_wrapper/` | Android arm64 一次性 wrapper 源码 |
| `misc/` | 品牌/槽位切换所需固定镜像 |
| `platform-tools/` | 随 Windows standalone 发行的 ADB/Fastboot |
| `workspace/` | 用户运行时导出、日志和临时操作目录；普通清理不得删除 |
| `dist/` | Windows 发布输出；旧目录不能作为新源码的验证证据 |
| `server/` | 已冻结的在线服务端实现 |
| `material/` | 操作者素材根：preload store、解压私钥、Xingc 密钥与补丁目录、fastboot artifact（见 `docs/MATERIAL.md`），不随仓库分发 |

发行审计保留对已删除持久 Root 资源、明文 preload、Xingc 私密材料、调试符号和壳中间物（`.vmp`/`.map`/`.pdb`）的
负向拒绝规则。这些命中项是泄漏，不是可恢复的产品功能。

### 稳定期机型维护方向

Offline 已进入稳定期。下一次增加机型时，优先保持客户端和 flow 不变，只补签名目录、受保护
payload、精确 runtime 配置和测试。当前 `devices.yaml` 与 `tools/embed_preloads.py` 存在重复注册；
后续可以在构建端引入 `platforms / payloads / runtimes / devices` 四张 YAML 表，由校验工具完成：

- profile、runtime、payload 的 `platform_id` 一致性；
- payload 固定 SHA-256、arm64 ELF 类型，以及 `preload=ET_DYN`、`exec=ET_EXEC`；
- 重复 alias、未知引用、绝对路径、argv、环境变量名、`su` 路径和分区配置检查；
- 展开为当前客户端已经使用的签名扁平目录，并生成改动摘要供人工复核；
- 仅在新增或替换 payload 时重建 wrapper，单纯复用既有 runtime 不重生成保护材料。

该整理只允许改变维护输入和构建工具，不允许让发行客户端读取松散 YAML、在运行时继承模板，
或根据“同平台”自动猜测兼容性。第一次迁移必须证明现有 13 个 profile、9 份唯一 runtime 的
展开结果语义一致；在真正增加下一批机型之前，没有必要为了目录好看单独启动这项迁移。

## 6. 测试与源码 smoke

先运行修改范围最接近的测试，再运行完整套件。Windows 系统临时目录 ACL 异常时，指定仓库内
唯一的 `--basetemp`，不要复用残留目录。

```powershell
python -m pytest -q `
  tests\test_session_free_flows.py `
  tests\test_transports.py `
  tests\test_workspace.py `
  tests\test_gui.py `
  tests\test_root_feature_removed.py

python -m pytest -q tests --basetemp .pytest-maintainer-<unique>

$env:QT_QPA_PLATFORM = "offscreen"
python tools\smoke_test.py
```

不要在维护文档中固定测试通过数、EXE 大小或哈希；这些易过期结果只应在当次交付记录或
`docs/PROJECT_STATE.md` 中按实际命令维护。

## 7. 离线构建与 SFX

标准顺序：

```powershell
.\scripts\build.ps1 -Channel Offline
python tools\audit_distribution.py dist\nebumtk_unlock_tools.dist
.\scripts\package-sfx.ps1        # 可选
```

构建步骤生成 Nuitka standalone、`release-channel.txt` 和可信源
`release-sha256-manifest.json`，随后运行 distribution audit。开源版没有加壳阶段，也没有
壳 receipt：审计直接对 Nuitka 输出执行。构建脚本在 `material/payloads/manifest.json`
存在时校验 payload store，没有 store 就跳过并提示。

关键发行路径和目录树必须拒绝 symlink、Windows reparse point 与 junction。发行目录禁止
`.vmp`、`.map`、`.pdb` 和 `*.vmp.exe`（历史中间物一旦出现就是错误，不是可恢复的功能）。
SFX 不加密码，并在目标同目录生成随机临时文件，完成 RAR 测试、真实解压和 distribution
audit 后才替换最终文件。

构建链只是打包便利，不是保护层：Nuitka 与 SFX 都不能保证管理员级攻击者无法 dump 或
篡改，发行文档里不要出现这种声称。

## 8. 在线冻结边界

在线源码当前只用于保留已验证过的接口设计：账号/机器绑定、策略/强制升级、单次 preload 授权和
服务端 Xingc 签名。恢复开发前必须重新执行 `docs/ONLINE_PREPRODUCTION_CHECKLIST.md`，生成全新的
生产配置与密钥，并为 Online 单独审核 VMP profile。当前 policy 没有 Online 项，Online VMP 必须失败，
不能回退复用 Offline profile。

在线客户端不得包含 Xingc 私钥、离线 preload 解包材料或服务端 payload；本地开发 HTTP、测试账号、
开发响应公钥和 `local-dev/` 数据不得进入生产构建。

## 9. 交付要求

每次材料修改完成后必须给出：

- 实际修改文件；
- 实际执行的命令、退出码、通过/失败/跳过数；
- 未执行的硬件或 SFX 验证及原因；
- 对设备写入、受保护资源和发行包是否产生影响；
- 已将 `docs/PROJECT_STATE.md` 精简为当前事实，而非追加对话日志。
