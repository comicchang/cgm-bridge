# CGMReader (Android Standalone BLE Frame Logger)

备份通路 B（Route B）：运行于 Android 系统上的独立原生 BLE 帧记录器（面向持续葡萄糖监测硬件 SiSensing ECO CGM）。  
与官方应用（`com.sisensing.eco`）补丁主通路（路线 A）完全解耦，直接与传感器低功耗蓝牙通信，抓取原始通信帧并落盘。

---

## 1. 功能特性 (v0)

- **扫描与连接**：基于 `android.bluetooth.le` 扫描匹配目标设备名的 CGM 外设，建立 LE GATT 物理连接。
- **服务发现与通知订阅**：
  - 主通信服务：`0000ff30-0000-1000-8000-00805f9b34fb` (`FF30`)
  - 写指令特征：`0000ff31-0000-1000-8000-00805f9b34fb` (`FF31`, Write / Write Without Response)
  - 数据通知特征：`0000ff32-0000-1000-8000-00805f9b34fb` (`FF32`, Notify)
  - 设备信息服务：`0000180a-0000-1000-8000-00805f9b34fb` (`180A`)
  - 电池服务：`0000180f-0000-1000-8000-00805f9b34fb` (`180F`)
  - 显式向 CCCD 描述符（`00002902-0000-1000-8000-00805f9b34fb`）写入使能值以开启固件推送。
- **帧落地**：十六进制格式记录全部 TX/RX 帧至应用私有存储目录 `ble_frames.log`（`/data/data/com.cgmbridge.reader/files/ble_frames.log`）并同步输出至 Logcat（TAG: `CGMReader`）。
- **原生 UI 控制台**：单 Activity 纯代码布局，实时回显连接状态、动态设置目标设备名、支持测试指令发送与最近 200 条通信帧流式展示。

---

## 2. 关键设计与经验对齐

与 iOS 端 CGMReader 踩坑经验完全对齐：

1. **Eager Initialization（急切初始化）**：
   在任何 BLE 扫描和 GATT 实例构造前，必须首先调用 `FrameLog.init(context)` 完成日志文件创建与追加写入句柄打开；否则蓝牙就绪与扫描回调可能先于文件句柄就绪触发，导致首批日志静默丢失。
2. **CCCD 描述符显式写入**：
   Android 平台仅调用 `setCharacteristicNotification(char, true)` 只影响本地协议栈过滤，必须同时向 `0x2902` CCCD 写入 `ENABLE_NOTIFICATION_VALUE` 才能通知外设固件开始上报数据。
3. **Android 13+ (API 33) 兼容**：
   适配了 API 33+ 新增的 `onCharacteristicChanged(gatt, char, value)` 与 `writeCharacteristic(char, value, type)` 签名，并对 API 26..32 保持向后兼容。

---

## 3. 权限要求与声明

在 `AndroidManifest.xml` 中进行了分版本权限隔离：

- **Android 12+ (API 31+) 运行时权限**：
  - `android.permission.BLUETOOTH_SCAN`（标记 `usesPermissionFlags="neverForLocation"`）
  - `android.permission.BLUETOOTH_CONNECT`
- **Android 11 及以下 (API <= 30)**：
  - `android.permission.BLUETOOTH`（`maxSdkVersion="30"`）
  - `android.permission.BLUETOOTH_ADMIN`（`maxSdkVersion="30"`）
  - `android.permission.ACCESS_FINE_LOCATION`（`maxSdkVersion="30"`）
  - `android.permission.ACCESS_COARSE_LOCATION`（`maxSdkVersion="30"`）

应用启动时自动检测并弹窗请求必要权限。

---

## 4. 互斥运行提示（重要：单连接限制）

- **硬件独占性**：CGM 硬件传感器固件采用单连接模型（Single Connection）。
- 同一时刻传感器仅允许单一主机/应用保持 BLE 连接。若官方应用（或路线 A 补丁应用）正在前台或后台连接传感器，CGMReader 将无法发现或连接设备；反之亦然。
- **运行模式与连接生命周期**：当前 v0 独立读取器为纯前台 Activity 原生实现，**无后台 Service，退出 Activity 即断开 BLE**（`onDestroy` 中自动执行 `disconnect()` 与 `close()` 释放 GATT 连接）。后台常驻与保活服务属后续路线图规划项。
- **推荐操作**：运行 CGMReader 前，请确保官方应用已完全强制停止（`am force-stop com.sisensing.eco`）或关闭蓝牙占用，避免连接抢占。

---

## 5. 参数配置

目标外设广播名称支持以下方式提供：

1. **构建配置默认值**：在 `app/build.gradle.kts` 中通过 `TARGET_BLE_NAME` 指定，默认占位符为 `"<BLE_NAME>"`。
2. **界面动态输入**：应用界面提供 `Target BLE:` 输入框，可直接修改目标名称并点击 `Scan` 立即生效。

---

## 6. 构建与安装验证

### 构建环境
- JDK 17+
- Android SDK（支持 `compileSdk 36`，已包含 `platforms/android-36` 与 `build-tools/34.0.0` / `36.0.0`）

### 编译 APK
```bash
export JAVA_HOME=/path/to/jdk-17
export ANDROID_HOME=~/Library/Android/sdk

cd apps/android-cgmreader
./gradlew assembleDebug
```

> **实机构建验证结果**：  
> `BUILD SUCCESSFUL in 17s (35 actionable tasks: 35 executed)`  
> 生成产物：`app/build/outputs/apk/debug/app-debug.apk`

### 安装与运行
```bash
# 安装到已连接设备
adb install -r app/build/outputs/apk/debug/app-debug.apk

# 实时查看日志
adb logcat -s CGMReader

# 提取设备端落盘帧日志
adb exec-out run-as com.cgmbridge.reader cat files/ble_frames.log > ble_frames.log
```

---

## 7. 版本路线图 (Roadmap)

- **v0（当前已交付版本）**：最小工程 BLE 帧记录器。完成扫描、连接、发现服务、订阅 FF32、落盘原始 hex 通信帧至 `ble_frames.log` 与 Logcat。
- **v1（规划中：协议应答机）**：按 `ble-protocol.md` 实现握手认证、传感器激活与历史/实时遥测数据拉取命令序列（`cmdId: 0x01` ~ `0xF0` 应答机与 CRC 校验）。
- **v2（规划中：算法引擎）**：集成逆向工程提取之 `ALGORITHM E1.1.5N(2025_09_02)` 换算逻辑，将原始电流/阻抗物理量换算为血糖浓度（mmol/L 与 mg/dL）及趋势。
- **v3（规划中：系统级健康网桥）**：对接 Android Health Connect（`BloodGlucoseRecord`），设计上采用与 iOS HealthKit 对齐的 `SyncIdentifier` 原则——基于 `(sensor_id, sample_timestamp)` 生成确定性幂等唯一键，以达成跨路线（路线 A 补丁导出与路线 B 独立直连）幂等去重（属 v3 规划，未交付；Health Connect 跨应用系统级合并行为未经验证）。

---

## 8. 日志隐私

`ble_frames.log` 与 Logcat 输出包含运行时蓝牙标识（传感器 MAC 地址、广播名等，均为运行时抓取值而非仓库内置）；对外分享日志前请自行脱敏。

---

## 9. 免责声明

本项目仅供个人数据互操作性与协议研究，非官方工具，不提供任何医疗准确性担保。传感器硬件与通信协议归原厂商所有。用户应自行承担使用补丁或独立客户端导致的所有风险。
