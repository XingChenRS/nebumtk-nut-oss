# 署名、溯源与反直抄

这不是装饰。AGPL-3.0-only §5 要求保留所有声明；一个把声明删掉的发行版不是合法副本。
本文件记录本项目埋了哪些标记、怎么查、查到之后怎么办。

## 1. 标记清单

| 位置 | 内容 | 作用 |
|---|---|---|
| 每个第一方 `.py` 前两行 | `SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>` + `SPDX-License-Identifier: AGPL-3.0-only` | 逐文件归属，REUSE 兼容；删掉即失去合法副本身份 |
| 每个第一方 `.py` 末行 | `# provenance: NBMTK-canary-7f3a91c4e8b2d05f` | 文件级水印：整文件照搬（最常见）会带着它；删头和删它要动每一处 |
| `COPYRIGHT` | 版权行 + 上游地址 + SPDX + 溯源 token + "§5 要求保留声明" | 署名文件；`LICENSE` 只放 AGPL 原文，保持扫描器可识别 |
| `nebumtk_unlock_tools/__init__.py` | `PROVENANCE_MARK`、`PROVENANCE_TOKEN`、`license_notice()`、`check_provenance()` | 代码内的机器可读标记，并随编译进二进制 |
| `python -m nebumtk_unlock_tools --license` | 打印 banner（含 token 与版权行） | 把声明变成**可观测行为**：删掉标记的改版连这条命令都答不出来 |
| `README.md`、`NOTICE.md`、本文件 | token 明文 | 抄文档也会带走 token |

`PROVENANCE_TOKEN = NBMTK-canary-7f3a91c4e8b2d05f`

这个 token 被刻意做成"没人会自己写出来"的形状：它不是 UUID，也不是单词，改名、换包名、
重排版都不会顺手消掉它；而它出现在包根、入口、工具、许可、文档与每个文件的末尾，想清干净
得逐处手改。

`PROVENANCE_TOKEN` 这一行是**水印**不是装甲：把文件整段复制走的人会带着它，逐行重写的人
不会——后者由下一节的结构比对负责。

## 2. 自己这棵树的闸门

```powershell
python tools\audit_provenance.py --self .
```

检查：每个第一方 `.py` 的两行头、包根的 marks、`LICENSE` 的 AGPL 正文与版权行与 token、
`README.md` / `NOTICE.md` / 本文件的 token。`--json` 给机器读；有违规退出码 1。发行前必须
是 0。

## 3. 查别人有没有直抄

```powershell
# 源码树、wheel、解包后的发行目录都行
python tools\audit_provenance.py --scan <path>

# 直接扫二进制（.exe 里的 mark 是明文 ASCII，能 grep 到）
python tools\audit_provenance.py --scan NEBUMTK_UNLOCK_TOOLS.exe
```

它按四类证据判定，命中即退出码 0（有证据）：

1. `PROVENANCE_MARK` / `PROVENANCE_TOKEN` 原样出现（含每个文件末行的水印）；
2. 我们代码里那些"不会撞车"的字面量（协议 AAD 前缀、`NEBUMTK-XINGC-PROTOCOL-PERMIT-v1`
   之类）成片出现；
3. 文件里仍留着 SPDX 头（对方连头都没清）；
4. **结构比对**：把目标的实质性代码行（去空白、去注释、≥24 字符）与我们第一方代码的
   行哈希集合求交集，单文件命中 ≥8 行且 ≥50% 即记为证据，并报出比例。

第 4 类专门对付"改了文件名、删了头、换了包名"的抄法——实测把 `lk/ubl/patcher.py`
去掉头和 token 行、改名成 `generic_patch.py` 后，仍然报 67/67 行（100%）。

退出码约定：`0` = 有证据，`2` = 干净，`1` = 用法错误。这样可以直接塞进脚本做批量巡查。

## 4. 发现未授权发行之后

1. 用 `--scan --json` 存证（含路径、文件名、命中的标记），连同时间戳一起留档。
2. 对照对方的 LICENSE/README：删了声明 → 违反 AGPL §5；不分发源码 → 违反 §4/§6；
   做成在线服务不给源码 → 违反 §13。
3. AGPL §8：未按许可使用即自动终止授权，且没有"补票"窗口，除非版权方另行书面恢复。
4. 平台投诉（GitHub DMCA / 应用商店）时把 `--scan` 报告作为证据附上；token 能在对方的
   二进制里被搜到，比"代码看起来很像"有力得多。

## 5. 不要把标记当成防破解

这些是**许可与取证**手段，不是技术保护：任何人都能改自己的副本（改了就失去许可，仅此
而已）。真正拦住商业转售的是"AGPL + 可证明的抄袭证据 + 平台投诉"，不是混淆或加壳。

## 6. 维护约定

- 新增第一方文件：两行 SPDX 头（见 `tools/audit_provenance.py` 的常量）。
- 新增"不会撞车"的字面量（协议前缀、challenge 标记、错误码）：顺手加进
  `tools/audit_provenance.py` 的 `FINGERPRINTS`。
- 不要动 `native/preload_wrapper/vendor/`、`nebumtk_unlock_tools/lk/vendor/` 与生成的
  `embedded_catalog.py` 的头：那是第三方的声明，改了才是真的侵权。
