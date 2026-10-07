# SiSensing ECO Android 端补丁与逆向分析指南

> **免责声明**：
> 本文档仅用于个人医疗设备数据互操作性与传感器寿命研究，不与任何商业公司关联。
> 官方 APK、Dex 字节码及 Native 算法库属于厂商版权资产，本文档及代码仓库均**不附带、不提供任何厂商二进制产物**。
> 所有分析输入均需使用者从自身合法拥有的物理设备副本中提取。
> 修改应用可能超出厂商设计预期或违反软件使用协议，由此造成的测量误差、硬件损坏或法律责任均由使用者自行承担。

---

## 一、官方 APK 溯源与哈希记录

通过对官方公开 Android 渠道溯源，确认 SiSensing ECO 官方 Android Companion App 的基本信息如下：

- **官方应用名称**：硅基轻享 (SiSensing ECO CGM)
- **应用包名**：`com.sisensing.eco`
- **官方渠道来源**：腾讯应用宝 (Tencent MyApp)
- **应用版本**：`02.26.01.00`
- **SHA-256 校验和**：`9a9f1e9fde51740c69f229b8a85771c785c82d9ec1ddea634527071cd8e0780d`
- **本地工作目录**（不入库）：`<WORKDIR>/sisensing-android/com.sisensing.eco_02.26.01.00.apk`

> **仓库隔离约定**：根据脱敏与版权合规要求，官方 APK 原包及解包后得到的 `.dex`、`.so` 产物均保存在本地工作目录中，**严禁提交入库**。

---

## 二、生命周期限制与架构映射 (Android vs iOS)

开源社区（如 `Juggluco`、`chalimov/sibionics_cgm_ha`）和固件逆向已经证实：SiSensing GS1/ECO 传感器在 14 天（336 小时）后**硬件与电池并未停止工作**，其 BLE 物理广播持续可用至 21~24 天（约 34560 个有效采样点）。14 天过期限制主要由客户端业务层在本地设卡。

通过对 iOS Mach-O（P1–P5）与 Android 平台代码交叉分析，定位出 Android 端对应的 5 个关键候选补丁点：

| 补丁编号 | 对应 iOS 站点 | 逻辑层级 | Android 对应目标与符号 | 模式特征 (Smali / 逻辑) | 补丁方案 (目标 24 天) |
|---|---|---|---|---|---|
| **A1** | P1 (`isOpenLimitExpiration`) | BLE 协议层 | `LocalBleService` / `a` | 序号比对 `index > 20161` (14×1440+1) 时丢弃数据包 | 将比较常量 `0x4ec1` (20161) 放宽至 `0x8701` (34561) 或旁路跳转 |
| **A2** | P2 (`getMaxGlucoseDay`) | 数据模型层 | `BleModel.getSensorDays()` | 方法返回有效天数常量 `14` (`const/16 v0, 0xe`) | 覆写返回值 `const/16 v0, 0x18` (24) |
| **A3** | P3 (`getMaxGlucoseNum`) | 数据模型层 | `BleModel.getSensorNum()` | 方法返回最大点数 `20160` (`const v0, 0x4ec0`) | 覆写返回值 `const v0, 0x8700` (34560 = 24×1440) |
| **A4** | P4 (`default_days_dword`) | 时间计算层 | `BsMonitoringEcoFragment` 时间差比对 | 14 天毫秒常量 `1209600000L` (`0x48190800L`) | 替换为 24 天毫秒常量 `2073600000L` (`0x7b98a000L`) |
| **A5** | P5 (`probeExpiration`) | UI / 状态机 | `BsMonitoringEcoFragment.dealSonserStatusExpire()` | 判定超期触发 `BsSonserExpirePop` 弹窗并更新状态为过期 | 入口覆写 `return-void` 阻断弹窗与只读状态转换 |

---

## 三、Smali 候选站点模式与分析

### 3.1 站点 A1：BLE 序号截断门禁
- **类名**：`com.sisensing.common.ble.LocalBleService` / 内部 BLE 数据分发助手 `com.sisensing.common.ble.a`
- **原代码模式**：
  ```smali
  # 检查当前数据包 index 是否超出 14 天上限 (20160 / 20161)
  const/16 v0, 0x4ec1
  if-gt v1, v0, :cond_drop_packet
  ```
- **补丁目标**：
  ```smali
  const v0, 0x8701    # 34561 (24 * 1440 + 1)
  if-gt v1, v0, :cond_drop_packet
  ```

### 3.2 站点 A2：传感器有效天数常量 (`getSensorDays`)
- **类名**：`com.sisensing.common.ble.BleModel`
- **原代码模式**：
  ```smali
  .method public static getSensorDays()I
      .registers 1
      const/16 v0, 0xe    # 14 天
      return v0
  .end method
  ```
- **补丁目标**：
  ```smali
  .method public static getSensorDays()I
      .registers 1
      const/16 v0, 0x18   # 24 天
      return v0
  .end method
  ```

### 3.3 站点 A3：传感器最大点数常量 (`getSensorNum`)
- **类名**：`com.sisensing.common.ble.BleModel`
- **原代码模式**：
  ```smali
  .method public static getSensorNum()I
      .registers 1
      const v0, 0x4ec0   # 20160 (14 * 1440)
      return v0
  .end method
  ```
- **补丁目标**：
  ```smali
  .method public static getSensorNum()I
      .registers 1
      const v0, 0x8700   # 34560 (24 * 1440)
      return v0
  .end method
  ```

### 3.4 站点 A4：时间戳毫秒过期窗口
- **类名**：`com.sisensing.bsmonitoring.BsMonitoringEcoFragment`
- **原代码模式**：
  ```smali
  # 14 天毫秒数 = 14 * 86400000 = 1209600000L
  const-wide v0, 0x48190800L
  ```
- **补丁目标**：
  ```smali
  # 24 天毫秒数 = 24 * 86400000 = 2073600000L
  const-wide v0, 0x7b98a000L
  ```

### 3.5 站点 A5：状态机与弹窗拦截 (`dealSonserStatusExpire`)
- **类名**：`com.sisensing.bsmonitoring.BsMonitoringEcoFragment`
- **行为**：在传感器到达阈值后，该方法负责弹出 `BsSonserExpirePop`，向 EventBus 发送 `RxBusSensorExpireBean`，并将本地设备状态锁定为只读或不可用。
- **补丁目标**：
  直接在方法入口注入 `return-void`，或在条件判断处令超期判定恒为 `false`，从而保持主界面持续接收与绘制血糖曲线。

---

## 四、Native 动态库与核心算法线索

Android 版应用将血糖计算与灵敏度解密抽取到了 Native 动态库中，打包在 `lib/arm64-v8a/` 目录下：
- **核心算法库**：`libnative-algorithm-E115N.so` / `libnative-algorithm-jni-E115N.so`（对应算法版本 `ALGORITHM E1.1.5N(2025_09_02)`）
- **核心导出符号**：
  - `Java_com_algorithm_e115n_NativeAlgorithmLibraryE115N_initEcoAlgorithmContext`
  - `Java_com_algorithm_e115n_NativeAlgorithmLibraryE115N_processAlgorithmContext`
  - `Java_com_algorithm_e115n_NativeAlgorithmLibraryE115N_decryptSensitivity`
  - `Java_com_algorithm_e115n_NativeAlgorithmLibraryE115N_getBinaryStructAlgorithmContext`
- **技术启示**：底层 Native 算法库只负责基于原始电流值（Current Raw Counts）和滤波参数输出瞬时血糖数值，并不包含 14 天主动断连逻辑；天数截断与状态机全部位于上层 Java/Kotlin 业务层。

---

## 五、补丁脚本用法与自适应门禁

仓库提供了标准自动化脚本 `tools/android/patch_official_apk.py`。

### 5.1 脚本使用步骤
```bash
# 1. 确保安装依赖环境
brew install apktool

# 2. 运行一键补丁（自动检测 SDK 工具链、校验白名单、覆写 Smali、对齐并重签名）
python3 tools/android/patch_official_apk.py \
  --input /path/to/com.sisensing.eco.apk \
  --output /path/to/com.sisensing.eco.patched.apk \
  --config tools/android/patch_config.example.json \
  --days 24 \
  --report patch_report.json
```

### 5.2 自适应版本门禁 (Fail-Closed)
脚本设计遵从安全工程的 Fail-Closed 原则：
1. 校验输入 APK 的包名与版本号，若不在配置白名单中，拒绝执行；
2. 计算输入 APK 的 SHA-256，与白名单哈希比对。若发生任何不匹配（哪怕 1 字节差异），立即中止；
3. 对每个 Smali 补丁站点执行严格的前置模式匹配，只有在原始模式精准命中时才允许写入。

### 5.3 模拟自测验证 (Mock Mode)
在没有输入真实安装包时，可通过 `--mock-test` 运行全流程模拟自测：
```bash
python3 tools/android/patch_official_apk.py --mock-test
```
自测将覆盖：
1. 非白名单 APK / 校验和错误的门禁阻断验证；
2. 虚拟 Smali 语法树的模式匹配与替换断言（14 -> 24 及 20160 -> 34560）；
3. 结构化 JSON 报告生成。

---

## 六、已知风险与应对方案

### 6.1 网易易盾加壳风险 (Packer Integrity Risk)
经深度逆向发现，官方发布版本（如 `02.26.01.00`）使用了**网易易盾加固（NetEase YiDun）**保护：
- **特征**：`classes.dex` 仅包含 30 个易盾壳代理类（`com.netease.nis.wrapper.*`），真正的 2700+ 个业务类被加密整合在 DEX 尾部附加数据及 `assets/nedata.db` 中；
- **运行时机理**：应用启动时由 `libnesec.so` 校验 APK 完整性、签名证书以及包体哈希。若通过 apktool 解包、修改并重签名，启动时易盾壳会检测到签名不一致而触发退出或闪退。

### 6.2 推荐工程应对路线

针对加壳保护，有三条可行应对路线：

```text
               ┌── 路线 A: 寻找未加固早期版本 / 社区版 ──► 直接执行静态 apktool 打补丁
               │
加固保护应对 ──┼── 路线 B: 动态脱壳 (Frida dexdump / FART) ──► 还原解密 DEX ──► 静态重打包
               │
               └── 路线 C (推荐): 运行时 Hook (LSPosed / Frida) ──► 不破坏原 APK 签名，规避易盾检测
```

1. **路线 C：运行时 Hook（推荐，零修改 APK）**：
   - 使用基于 LSPosed 或 Frida 的 Xposed 模块，在应用启动后直接 Hook 目标方法：
     - Hook `com.sisensing.common.ble.BleModel.getSensorDays()` 恒返回 `24`；
     - Hook `com.sisensing.common.ble.BleModel.getSensorNum()` 恒返回 `34560`；
     - Hook `com.sisensing.bsmonitoring.BsMonitoringEcoFragment.dealSonserStatusExpire()` 为空实现；
   - **优势**：原版 APK 签名与易盾校验完全不受破坏，更新方便且稳定可靠。
2. **路线 B：动态内存 Dump 重打包**：
   - 在已 Root 设备或模拟器中使用 `frida-dexdump` 或 `FART` 脱壳，提取解密的 `classes*.dex`；
   - 将解密后的 DEX 还原为标准 Smali 目录，通过 `patch_official_apk.py` 进行静态覆写后重新签名。
3. **路线 A：未加固历史/社区版本**：
   - 早期历史版本（部分分发渠道未接入易盾）或海外社区未加固版本可直接使用本工具一键修补。

### 6.3 服务端云端反制风险
- 传感器绑定激活时，服务端会记录初始激活时间点；
- 满 14 天后，若服务端在云端强行将设备状态标记为过期并推送至客户端，可能导致云端历史记录同步异常；
- 建议在传感器第 14 天到期**前**启用补丁或 Hook 策略，确保客户端本地状态机始终维持在正常采集中。
