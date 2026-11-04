# NEBUMTK 离线版预生产验收

本清单用于当前离线交付。它不是历史测试报告；每次候选版本都必须重新填写实际命令、产物哈希和
设备结果，不能沿用旧 `dist/` 或旧通过数。

## 1. 候选版本边界

- [ ] 构建来源仅为本仓库当前源码。
- [ ] `BUILD_CHANNEL` 和 `release-channel.txt` 均为 `offline`。
- [ ] GUI 没有账号/网络入口、持久 Root 管理或投屏入口。
- [ ] 发行目录不含管理器 APK、`libksud.so`、scrcpy 运行时或相关正向配置；`sakisu/` 只含审阅过的 Windows 主机侧 patcher 及其 manifest。
- [ ] 临时提权入口仍可用；`init_boot`、`vendor_boot` 只作为 profile 指定的原机备份项。
- [ ] 普通模式只使用签名机型目录和内置受保护 preload；测试模式没有放宽危险写入边界。

## 2. 源码与回归

建议使用全新仓库内临时目录：

```powershell
python -m pytest -q `
  tests\test_root_feature_removed.py `
  tests\test_pd2324_profile.py `
  tests\test_new_preload_profiles.py `
  tests\test_offline_preload_grant.py `
  tests\test_adb_device_challenge.py `
  tests\test_backup_manifest.py `
  tests\test_session_free_flows.py `
  tests\test_transports.py `
  tests\test_workspace.py `
  tests\test_gui.py

python -m pytest -q tests --basetemp .pytest-offline-acceptance-<unique>
```

- [ ] 定向测试退出码为 0，并记录通过/失败/跳过数。
- [ ] 完整测试退出码为 0，并记录通过/失败/跳过数。
- [ ] 任何跳过项均有明确平台或设备原因，不把跳过等同于通过。
- [ ] `QT_QPA_PLATFORM=offscreen` 的源码 smoke 能稳定启动，且没有污染待发布目录。

## 3. 资源与安全边界

- [ ] 签名机型目录可加载，篡改目录、签名或 profile 后 fail closed。
- [ ] 明文 `devices.yaml`、`preloads/`、`xingc_sources/` 不在发行目录。
- [ ] 普通模式拒绝外部明文 preload；受保护 wrapper 的认证、截断和错误 grant 用例均失败。
- [ ] 每次重试生成新设备挑战和新 grant；失败后不复用旧授权或原始 CEK。
- [ ] wrapper 只走匿名内存执行，没有命名明文 payload fallback。
- [ ] 成功、失败、超时、设备掉线和取消都清理远端 wrapper。
- [ ] UI 和持久日志不记录密钥、grant、Xingc token、完整敏感命令、设备路径或多余 serial。
- [ ] distribution audit 对已删除持久 Root 资源、明文 SO/ELF、私钥、调试符号和 VMP 中间件保持负向拒绝。

## 4. 逻辑与设备实测

在专用测试设备上逐项记录机型、固件、初始槽位和实际结果：

- [ ] ADB/Fastboot 多设备、offline、unauthorized 和序列号变化均 fail closed。
- [ ] 临时提权能成功；故意破坏 wrapper/grant 后完整重试并最终清理。
- [ ] 备份目录带设备序列号，`backup-manifest.json` 原子生成并包含 profile、ADB serial、槽位、
  分区实际名称、文件名、大小和 SHA-256。
- [ ] LK、`init_boot`、`vendor_boot` 等 profile 备份项均是原机数据；不存在自动修补或安装持久 Root 的行为。
- [ ] 手选未 patched 的 Vivo LK 在提权和传输前被拒绝；合法 patched LK 才能继续。
- [ ] 写入前重新核对实时 serial、模式、槽位、分区容量和冻结输入哈希。
- [ ] raw LK 仅允许结构匹配且在实现阈值内的 cert2 小幅增长，零填充 slot 镜像保持原长度。
- [ ] Vivo Xingc、公钥嵌入、双槽同步、OPPO unlock/已解锁短路和本机备份还原分别通过。
- [ ] 还原 LK 时篡改 manifest、换设备、换 profile、改文件名/大小/哈希均被拒绝。
- [ ] 取消、超时、部分双槽写入失败和重连都有明确结果，不出现按钮重入或并发设备操作。

## 5. Nuitka 候选包

```powershell
.\scripts\build.ps1 -Channel Offline
python tools\audit_distribution.py dist\nebumtk_unlock_tools.dist
```

- [ ] 构建脚本完成签名目录、受保护 preload、Xingc、platform-tools 和 libusb 检查。
- [ ] `release-sha256-manifest.json` 是构建步骤产生的可信源清单。
- [ ] 打包启动 smoke 在隔离临时副本执行，原发行目录在 smoke 前后审计一致。
- [ ] 记录 standalone 主 EXE 与 manifest 的 SHA-256；不复用旧记录。

## 6. SFX

```powershell
.\scripts\package-sfx.ps1
```

开源版没有加壳阶段：Nuitka standalone 审计通过后直接打 SFX。

- [ ] 关键路径和目录树不存在 symlink、reparse point 或 junction；包内没有 `.vmp`、`.map`、`.pdb`、`*.vmp.exe`。
- [ ] SFX 不加密码，脚本、日志与文档里也没有出现任何发行密码。
- [ ] SFX 在随机临时文件完成 RAR 测试、真实解压与 distribution audit 后才原子替换目标。
- [ ] `-Force` 失败时旧的可用 SFX 仍保留。

## 7. 最终签收记录

候选版本必须单独记录：源码版本/工作区状态、测试命令与计数、测试设备、standalone/SFX 哈希、
WinRAR 版本、未验证项及批准人。Nuitka、匿名内存和清零都只能提高提取成本，不能承诺管理员
或设备 root 权限下绝对不可 dump。
