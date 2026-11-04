# 参与开发

## 环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pytest==9.0.2
$env:QT_QPA_PLATFORM = "offscreen"
```

Python 3.10+；实际验证环境是 CPython 3.12 + PySide6 6.10.2。

## 提交前

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-basetemp
```

- 全量测试必须绿；依赖操作者素材的用例会 skip，**不要把 skip 改成"本地补素材再断言"**。
- 改品牌流程（Vivo/OPPO/Honor）时，先跑聚焦测试，再跑全量；顺序、守卫与 receipt
  不能变。
- 改发行链（`scripts/`、`tools/audit_distribution.py`）时，跑一次
  `python tools/audit_distribution.py <dist>`。

## 许可

本项目是 **AGPL-3.0-only**（见 [LICENSE](LICENSE)、[NOTICE.md](NOTICE.md)）：

- 提交即视为同意以同一许可授权你的贡献（inbound = outbound），不引入额外的 CLA。
- 新文件前两行固定写：

  ```python
  # SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
  # SPDX-License-Identifier: AGPL-3.0-only
  ```

  第三方代码放进
  `native/preload_wrapper/vendor/` 并保留其原始许可，不要给它们贴 AGPL 头。
- 不要引入只允许宽松再分发、却不能并入 AGPL 作品的依赖。新增依赖前先看它的许可。

## 不要做的事

- 不要重新引入访问密码或任何密码派生密钥（见 [AGENTS.md](../AGENTS.md)）。
- 不要提交 `material/`、`preloads/`、`xingc_sources/`、`local-dev/`、`.private/`、
  任何 `*.pem`/`*.key` 或设备固件。
- 不要在仓库里放明文 payload 或 Xingc 私钥。
- 不要加壳、不要加壳后 receipt、不要把壳产物写进 `dist/`。

## 提交信息

用 `area: 一句话` 的形式，例如：

```
core: revalidate live capacity before the final write
sakisu: pin the host patcher manifest hash
docs: state the material root layout
```

改两条 baseline 时在正文里写明改了哪条、为什么。

## 新增机型

1. `devices.yaml`（维护者输入）与 `model_catalog.yaml`（归一化五表）同步改；
2. 重新编译并签名目录：`python tools\build_device_catalog.py --regenerate`；
3. 若该机型需要新 payload：`preloads/<payload_id>/preload.so` 放好后
   `python tools\embed_preloads.py --regenerate`；
4. 补 `tests/` 里对应的目录/运行时断言；
5. 跑全量测试，并在真机上过一遍对应品牌流程。
