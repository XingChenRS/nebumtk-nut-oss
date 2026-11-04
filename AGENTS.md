# 开源仓库规则（1.1 baseline）

本文件是开发者和 agent 在本分支工作的持久契约。改任何东西之前先读它。

## 这是什么

纯本地的 Windows 工具：MediaTek v6 bootloader 解锁 + 分区备份/还原 + 品牌流程，单
Python/PySide6 应用，Nuitka standalone 分发。**无服务端、无账号、无网络、无访问密码。**

## 分支

- `baseline/1.1`（本分支，tag `v1.1.0`）= 最后发货的离线版，只读参考。
- `main`（tag `v2.0.0`）= 重构后的 2.0 基线。
- 两条线是孤儿分支，互不 merge。共享的安全/构建修复两边都改，并在提交信息里写明。
- 不要在 1.1 上加功能。要演进就去 `main`。

## 源码真相

- `nebumtk_unlock_tools/` 是产品本体，`tests/` 是可执行契约。
- `docs/PROJECT_STATE.md` 是当前交接文档：只写现在的架构、风险、已验证命令与待决事项。
- `docs/OFFLINE_RELEASE_DESIGN.md` 是发行/安全契约。
- `docs/MATERIAL.md` 是素材（preload store、artifact store、Xingc 密钥）布局。
- `docs/LICENSING_AND_PROTECTION.md` 记录"客户端保护能做到什么、做不到什么"。

## 硬边界

- **没有访问密码，没有密码派生密钥。** 不要再引入 `security.py` 式 master key 门禁，
  也不要让资源解密依赖操作者输入的字符串。材料用 `keymaterial` 从 `material/` 读，
  缺了报 `MaterialError` 并给出路径。
- **没有壳。** 构建链只有 Nuitka；VMProtect、receipt、加壳后审计都不是本仓库的一部分。
  发行目录审计仍拒绝 `.vmp`/`.map`/`.pdb`/`*.vmp.exe`。
- **没有在线通道。** 不要恢复 `nebumtk_unlock_tools/online/`、登录对话框或
  `NEBUMTK_CHANNEL` 环境开关；`build_profile` 只有 `offline`。
- **许可固定为 AGPL-3.0-only。** 不要改成 MIT/Apache/BSD，也不要加"或更高版本"；
  新文件首行带 `SPDX-License-Identifier: AGPL-3.0-only`；引入依赖前先确认它能并入
  AGPL（Qt/PySide6 是 LGPL-3，只允许动态链接）。第三方 vendor 代码保留原许可。
- 仓库不携带厂商素材：无 LK 镜像、无明文 payload、无 Xingc 私钥、无机型固件、无
  SakiSU patcher。
- 不提供持久 Root 管理：无 KernelSU/SakiSU/Magisk 管理器、无 `libksud.so`、无 root
  控制台。唯一例外是 `init_boot` 的 SakiSU 镜像补丁边界和可选的 Vivo 原槽写入。
- 临时提权（受保护 preload + 一次性 `su`）是备份与 system 侧分区写入的前提，不是
  Root 功能。
- 机型目录与机型包是签名制品；发行客户端不得从可编辑的 YAML/JSON 执行命令。
- 测试模式只放松机型/固件/payload 的匹配，绝不放松精确设备、传输状态、槽位、分区
  存在性与容量、冻结输入哈希、取消、终局写入复验。

## 安全与可靠性

- 有标准原语就用标准原语，不要自造密码学格式。
- 不要声称 Python、内存清零、匿名 fd 或任何客户端手段能阻止管理员级 dump。
- UI activity 文本保持脱敏（无设备路径、无详细利用命令、无秘密）；workspace 下的
  会话日志是本机诊断文件，保留命令、参数、设备路径与有界输出。密钥、token、签名、
  challenge 与长不透明 blob 两种形态都要打码。
- 破坏性设备操作必须在最终边界复验实时序列号、模式、槽位、分区容量、源哈希与取消。
- 没有有意义零填充尾的裸 LK 文件只能按结构验证过的 cert2 覆盖余量增长。
- 设备侧明文 payload 只允许通过匿名 fd/inode 立即执行，不得有具名回退。

## 改动纪律

- 每个按钮都不继承前一个 worker 的设备/产物授权：每次操作重新扫描、重新校验目标、
  重新读取将要写入的产物。
- 动品牌流程（Vivo/OPPO/Honor）时保持顺序、守卫与 receipt 不变。
- 小改动优于兼容分支；替代方案被证明可用后删掉被取代的代码、测试与文档。
- 保持五页 UI 拓扑：设备、分区与备份、Root 与解锁、镜像与恢复、工具与设置。

## 验证关卡

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q --basetemp=.pytest-basetemp
```

- `tests/conftest.py` 已经把设置目录（`NEBUMTK_SETTINGS_DIR`）与 material root 指到
  临时目录，测试不再读操作者机器状态。不要再让用例依赖 `%APPDATA%` 或真实
  `material/`。
- 依赖操作者素材的用例在素材缺失时 `pytest.skip`，不要改成断言失败。
- 没有命令输出或其它可复现验证结果，不要报告"完成"。
