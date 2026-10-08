# SiSensing ECO 官方 iOS App 补丁与重签名工具

本工具用于对官方 SiSensing ECO（`com.sisensing.eco`）解密版 IPA 进行二进制补丁与级联重签名，以解除传感器周期限制（扩展至 21 / 24 天）并修复后台崩溃问题。

---

## 免责声明（Disclaimer）

- **研究与互操作性目的**：本项目及工具仅用于个人健康数据互操作性与逆向工程学术研究，与应用及硬件厂商没有任何关联。
- **安全与合规风险**：对官方客户端进行二进制补丁修改超出了原厂设计用途，可能违反软件服务条款（ToS）并影响医疗设备精度认证。使用此补丁的一切后果与风险由使用者自行承担。
- **无附带二进制分发**：本仓库**不附带**任何厂商专有二进制文件、IPA、APK 或专有数据库。输入文件必须由合法购买并拥有相应设备与应用的使用者，从个人设备中自行提取已解密（砸壳）的 IPA 副本。

---

## 核心功能与补丁站点

补丁基于静态分析与 IDA Pro 逆向定位，共覆盖 7 处关键代码站点：

| 站点编号 | 标识符 | 默认偏移 (3.9.4) | 作用原理 |
|---|---|---|---|
| **P1** | `P1_isOpenLimitExpiration` | `0xB5A3DC` | 全局开关强制写 0（`STRB WZR, [X8]`），关闭 BLE 截断与错误码 109 报警门限 |
| **P2** | `P2_getMaxGlucoseDay` | `0x247B00` | 入口直接覆写 `MOV W0, #24; RET`，将允许最大天数扩展为 24 天 |
| **P3** | `P3_getMaxGlucoseNum` | `0x247714` | 入口直接覆写 `MOV W0, #34560; RET`（24 × 1440），匹配点数上限 |
| **P4** | `P4_default_days_dword` | `0xD01F84` | 覆写 `setBLEConfigWithString` 内建天数常量 dword（14 -> 24） |
| **P5a** | `P5a_growing_no_params` | `0x1D39D4` | `-[GrowingIOManager gj_GrowingNoParamsEvent:]` 入口覆写 `RET` |
| **P5b** | `P5b_growing_event` | `0x1D3A40` | `-[GrowingIOManager gj_GrowingEvent:andParams:]` 入口覆写 `RET`（防后台 SIGABRT） |
| **P5c** | `P5c_growing_event_kv` | `0x1D3AD0` | `-[GrowingIOManager gj_GrowingEvent:andKeyParams:andValueParams:]` 入口覆写 `RET` |

> **注**：P5 组（P5a–P5c）用于防御因 GrowingIO 未初始化在后台触发的 `NSException` / `SIGABRT` 崩溃。

---

## 门禁原理（Fail-Closed Gate Design）

为了杜绝因 App 版本不一致、二进制偏移漂移或二次修补污染导致的静默损坏或启动崩溃，脚本实施了三道严格的**Fail-Closed（故障闭合）防御门禁**：

1. **版本白名单校验**：解包后解析 `Payload/ECO.app/Info.plist`，比对 `CFBundleShortVersionString` 是否在 `patch_config.json` 的白名单支持列表中。若未注册则拒绝执行。
2. **纯净二进制哈希前置校验**：计算未修改主程序 `ECO` 的 SHA256 哈希值，与配置中记录的官方未修改基线哈希进行全量比对。若输入二进制已经被修改、签名污染或为未知编译版本，脚本立即终止退出，绝不执行任何修补。
3. **逐站点原始字节断言（Before Hex Assertion）**：在对各站点覆写前，读取偏移处精确字节序列与 `before` 期望值比对。如果期望字节不符（例如二进制已打过补丁或架构漂移），立即中止并保留错误上下文，防止产生半成品产物。

---

## 环境依赖

- **Python 3.8+**
- **ldid**（Link Identity Editor）:
  - macOS: `brew install ldid`
  - Linux: `sudo apt-get install ldid` 或从源码编译

---

## 使用方法

### 基本命令

输入纯净解密的 IPA，输出修补并重签名的 IPA：

```bash
python3 patch_official_ipa.py \
  /path/to/com.sisensing.eco_3.9.4_decrypted.ipa \
  /path/to/eco_3.9.4_patched.ipa
```

### 参数说明

```text
usage: patch_official_ipa.py [-h] [--config CONFIG] [--days {21,24}]
                             [--team-id TEAM_ID] [--report REPORT]
                             [--no-resign]
                             input output

positional arguments:
  input                 输入解密版 IPA 路径
  output                输出补丁与重签名后的 IPA 路径

options:
  --config CONFIG       指定 patch_config.json 路径（默认同目录下的配置文件）
  --days {21,24}        设置传感器周期天数（默认: 24）
  --team-id TEAM_ID     自定义 Apple 开发者 Team ID（默认: TEAMID 占位符）
  --report REPORT       生成验证报告路径（默认: patch_report.json）
  --no-resign           跳过 ldid 重签名阶段（仅测试/排查用）
```

### 签名范围说明（重要）

本脚本为保持 App 原有功能，在主二进制上保留了厂商原始 entitlements（含 Siri、APNs、associated-domains、app groups 等）。这些 entitlements **无法**用个人免费开发者证书签出——个人 provisioning profile 会拒绝它们。因此：

- **支持的安装环境**：越狱设备（AppSync Unified）或 TrollStore——这类环境不校验 provisioning，ldid 签名即可启动。
- **entitlement 不会被重签"授予"**：ldid 只是把 entitlement **声明**写入签名；越狱/TrollStore 因不校验 provisioning 才使其生效，而 APNs、Siri、app-groups 等服务的**服务器端授权**仍绑定厂商 Team ID——重签后相关服务不会正常工作，仅保留本地能力声明。
- **不承诺**：AltStore / Sideloadly / 个人 Apple ID 直装。该路径未经验证，大概率因 entitlement 校验失败而无法安装；如需该路线，请自行用 Xcode 重新配置可签的 entitlement 集合并替换脚本内嵌模板。
- `--team-id` 参数面向会自行管理完整 entitlement 的环境；默认 `TEAMID` 占位符仅供越狱/TrollStore 场景（ldid ad-hoc 亦可，可加 `--no-resign` 跳过）。

示例（越狱/TrollStore 场景）：

```bash
python3 patch_official_ipa.py \
  input_decrypted.ipa \
  output_patched.ipa \
  --team-id YOUR_TEAM_ID \
  --report patch_report.json
```

---

## 验证报告格式

执行完毕后，脚本自动生成包含 7 站点字节级审计的 `patch_report.json`：

```json
{
  "timestamp": "2026-10-07T12:00:00+00:00",
  "input_ipa": "/path/to/input.ipa",
  "output_ipa": "/path/to/output.ipa",
  "app_version": "3.9.4",
  "bundle_id": "com.sisensing.eco",
  "binary_name": "ECO",
  "binary_sha256_before": "575e738d4694b71c9ebda7c02bdd34909bde8618c0418608aefe07511247c621",
  "binary_sha256_after": "570036439ce7c65f2dff9c02c3d3e043f52398699e6a17c571c5ea61751a87a7",
  "team_id": "TEAMID",
  "days": 24,
  "status": "success",
  "sites_total": 7,
  "sites_patched": 7,
  "sites": [
    {
      "id": "P1",
      "name": "P1_isOpenLimitExpiration",
      "offset": "0xB5A3DC",
      "before": "02010039",
      "after": "1f010039",
      "status": "ok",
      "description": "isOpenLimitExpiration 全局开关强制写 0（关 BLE 截断/错误 109）"
    }
  ]
}
```

---

## 常见失败模式与排查

1. **`[FAIL] Gate check failed: Version 'X.Y.Z' is not in whitelist`**
   - **原因**：输入的 IPA 版本尚未在 `patch_config.json` 中定义。
   - **处置**：请参照下方“新版本贡献方法”更新或添加该版本的偏移配置。
2. **`[FAIL] Gate check failed: SHA256 mismatch for ECO`**
   - **原因**：二进制并非官方未修改的纯净解密副本，可能曾被打过补丁、被其他重签名工具修改、或者为不同 Build 构型。
   - **处置**：必须使用从 App Store 纯净导出的解密 IPA（例如通过 Frida dump 或 dumpdecrypted 提取的 pristine 版本）。
3. **`[FAIL] Site Px @ 0xXXXXXX mismatch!`**
   - **原因**：实际二进制在指定偏移处的字节不等于 `before` 模式；若提示 `match patch target`，说明输入文件已被修补过。
   - **处置**：不要在已修补的产物上二次修补；换用纯净解密 IPA 作为输入。
4. **`RuntimeError: ldid executable not found in PATH`**
   - **原因**：系统未安装 `ldid` 工具。
   - **处置**：macOS 执行 `brew install ldid`。
5. **iOS 设备端装机注意事项**
   - **越狱环境**：建议配合 AppSync Unified 使用；主程序与扩展均已重置 entitlements。
   - **TrollStore 环境**：可直接安装生成的 `.ipa`。
   - **锁屏蓝牙限制**：iOS 锁屏状态下 CoreBluetooth 后台扫描受系统策略节流，首次配对与发现建议在设备解锁状态下进行。

---

## 新版本配置贡献方法

当官方 App 发布更新时（例如 `3.9.5`），可通过以下流程提取新偏移并提交配置：

1. **解密与提取基线**：
   - 提取新版解密 IPA，解压得到 `Payload/ECO.app/ECO`。
   - 计算 SHA256：`sha256sum Payload/ECO.app/ECO`。
2. **IDA / Ghidra 逆向重定位 7 个站点**：
   - **P1**：定位 `isOpenLimitExpiration` 交叉引用，找到 `STRB W2, [X8]` 位点（替换为 `STRB WZR, [X8]` = `1F010039`）。
   - **P2**：定位 `-[... getMaxGlucoseDay]` 函数入口，获取文件偏移（`File Offset = VA - 0x100000000`）。
   - **P3**：定位 `-[... getMaxGlucoseNum]` 函数入口，获取文件偏移。
   - **P4**：定位 `setBLEConfigWithString` 中对立即数 `14` 的赋值或引用 dword。
   - **P5a–c**：定位 `-[GrowingIOManager gj_Growing*]` 三个方法入口，分别记录其原始函数序言（Prologue）字节。
3. **更新 `patch_config.json`**：
   在 `supported_versions` 下新增版本节点，填入版本号、`binary_sha256` 及 7 站点的 `offset`、`before`、`after`、`description`。
4. **实测验证**：
   使用本脚本实跑验证，确认 7 站点全部通过且生成报告。
