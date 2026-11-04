# 本地素材（material）

开源版没有访问密码，也没有随仓库分发的密文。凡是闭源版用 master key 解出来的东西，
开源版都当成操作者自己的本地文件读。**所有素材目录都在 `.gitignore` 里。**

## 1. 有效路径

解析顺序（`nebumtk_unlock_tools/keymaterial.py`）：

1. 显式传入的 `root=`（工具与测试）；
2. 环境变量 `NUT_MATERIAL_ROOT`；
3. `<项目根>/material`（默认）。

## 2. 目录布局

```text
material/
  wrap-private.pem             # RSA-3072 私钥：解封每个 payload 的 CEK（OAEP-SHA256）
  preload-pack.key             # 32 字节：机型包内 payload CEK 的对称密钥（hex/base64/raw 皆可）
  payloads/
    manifest.json              # 每个 payload 的 wrapper 哈希、OAEP label、被包裹的 CEK
    <payload_id>/wrapper.so    # 推到设备上的加密 wrapper（ELF，hash 固定）
  xingc/
    protocol-permit            # 内容为 NEBUMTK-XINGC-PROTOCOL-PERMIT-v1，标记协议许可
    verify-private.pem         # Xingc 签名私钥
    verify-public.avbpub       # 与私钥配套的 AVB public key blob（520 字节）
    patch-catalog.json         # 已审核的 LK 补丁目录
```

`material/payloads/manifest.json` 的形状：

```json
{
  "version": 1,
  "native_build_sha256": "<wrapper 源码与 NDK 组合的指纹>",
  "payloads": {
    "<payload_id>": {
      "plain_sha256": "<明文 payload 的 SHA-256>",
      "wrapper_sha256": "<wrapper.so 的 SHA-256>",
      "oaep_label": "<base64(NEBUMTK-WRAP-v1:<id>:arm64-v8a:35)>",
      "wrapped_key": "<base64(RSA-OAEP(CEK))>"
    }
  }
}
```

## 3. 生成步骤

```powershell
# 1) 明文 payload 放进 preloads/<payload_id>/preload.so，然后编译 wrapper
python tools\embed_preloads.py --regenerate
#    首次运行会生成 material/wrap-private.pem（3072 位 RSA），之后一直复用
#    校验已有 store：
python tools\embed_preloads.py

# 2) Xingc 材料（derive 自 xingc_sources/）
python tools\import_xingc_material.py
python tools\import_xingc_material.py --check

# 3) 机型包密钥（只有需要 catalog 内嵌 preload 时才用）
python tools\build_device_catalog.py --init-key
python tools\build_device_catalog.py --regenerate
```

`--regenerate` 需要 Android NDK 29.0.14206865（`ANDROID_NDK_HOME` 或
`%LOCALAPPDATA%\Android\Sdk\ndk\...`）。

## 4. 运行时行为

- 缺 `material/payloads/manifest.json` → 未登记的 payload 报 `KeyError`/`MaterialError`，
  消息里带绝对路径和补法。
- wrapper 哈希不符、不是 ELF → `MaterialError`，不会把文件推给设备。
- 缺 `material/xingc/*` → `MaterialError`，Xingc 相关按钮在流程开始前失败。
- `verify_pair()` 会比对 Xingc 私钥与 AVB public blob，不匹配直接拒绝。

## 5. 安全边界

这些文件在磁盘上就是明文/私钥，**只在操作者自己的机器上有效**。开源版不提供任何
"保护层"：谁拿到 `material/` 谁就拿到同样的能力。这是刻意的取舍——闭源版的
[LICENSING_AND_PROTECTION.md](LICENSING_AND_PROTECTION.md) 已经把结论写清楚：
客户端密码门禁在秘密已公开后收益接近零。
