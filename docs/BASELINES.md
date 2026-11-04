# 两个 baseline

本仓库用 git 分支承载两代产品，而不是在一个目录里放两份源码。

| 分支 | tag | 产品 | 说明 |
|---|---|---|---|
| `main` | `v2.0.0` | 2.0 | 当前基线。typed contracts、通用分区备份/还原、SakiSU `init_boot` 补丁边界。 |
| `baseline/1.1` | `v1.1.0` | 1.1 | 最后实际发货的离线版。`gui/toolbox_ui.py` + `gui/worker.py` 的单文件五页 UI。 |

两条分支都是 **orphan branch**（没有共同祖先）：它们是两套独立整理的源码树，强行
造一个共同父提交只会伪造历史。

## 并排查看

```powershell
git worktree add ..\nut-1.1 baseline/1.1
code ..\nut-1.1        # 另一份工作树
```

## 对比两代差异

```powershell
# 只看产品包
git diff baseline/1.1..main -- nebumtk_unlock_tools

# 只看契约层
git diff baseline/1.1..main -- nebumtk_unlock_tools/core

# 文件清单变化
git diff --stat baseline/1.1..main
```

## 两代的实质差别

| 主题 | 1.1 | 2.0 |
|---|---|---|
| UI | `gui/toolbox_ui.py` 内联布局，`gui/worker.py` 一个 worker | `gui/pages/*` 五页 + `gui/pages/common.py` 契约，每页独立 worker |
| 分区操作 | `core/partition.py` 单文件 | `core/partition.py` + `core/partition_checklist.py` + `flows/generic_partition.py` |
| 资源 | `resources/catalog.py` 自带密钥封装 | `resources/payload_store.py`（开源版）+ 可选的 catalog 内嵌 preload |
| SakiSU | `core/sakisu.py` + 内嵌 patcher 密文 | `sakisu/` 明文 patcher + `host_importer` 哈希固定，`init_boot` 补丁流为独立 typed flow |
| 测试 | 26 个文件 | 46 个文件（含 `test_capability_*`、`test_sakisu_patch_only`、`test_prepare_unlock_row`） |

## 维护约定

- 共享的安全/构建修复两边都要改；产品行为的演进只进 `main`。
- `baseline/1.1` 不再增加功能。它在 2026-10-06 之后就是只读参考。
