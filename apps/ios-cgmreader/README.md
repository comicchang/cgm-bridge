# CGMReader (iOS Standalone BLE Frame Logger)

备份通路 B：运行于越狱 iOS 上的独立 BLE 帧记录器（SiSensing ECO CGM）。
与官方 App 补丁主通路完全独立，直连传感器蓝牙，抓取原始通信帧并落盘。

---

## 功能说明 (v0)

- **扫描与连接**：通过 CoreBluetooth 扫描匹配目标设备名的 CGM 外设，或复用系统已持有连接（快路径）。
- **服务发现与通知订阅**：发现 `FF30`（主服务）、`180A`（设备信息）、`180F`（电池服务），订阅 `FF32`（Notify），就绪 `FF31`（Write）。
- **帧落地**：十六进制记录全部 TX/RX 帧至越狱固定路径 `/var/mobile/Documents/CGMReader/ble_frames.log`。
- **UI 显示**：精简 SwiftUI 列表实时显示最近事件日志与连接状态。

---

## 关键设计修复

1. **Eager Initialization**：
   `@StateObject` 默认使用 autoclosure 惰性求值。在 headless 执行或无渲染场景下，SwiftUI 场景若未实例化 body，会导致 `BLELogger` 从未初始化、日志与蓝牙逻辑从未运行。因此在 `CGMReaderApp.init()` 中显式急切构造。
2. **OpenLog Prior to CentralManager**：
   必须先调用 `openLog()` 完成文件句柄创建与打开，再初始化 `CBCentralManager`；否则蓝牙状态更新回调可能在主线程/BLE 队列抢先触发，导致首条日志写向 nil handle 丢失。
3. **No-Container 路径适配**：
   在 `com.apple.private.security.no-container` entitlement 下，标准 `documentDirectory` 沙盒 API 返回失效，直接硬编码越狱持久化路径 `/var/mobile/Documents/CGMReader/`。

---

## 参数配置

设备目标名称支持以下方式配置（优先级自高至低）：

1. **启动参数**：`--ble-name <BLE_NAME>`
2. **环境变量**：`CGM_BLE_NAME=<BLE_NAME>`
3. **默认占位**：`<BLE_NAME>`

---

## 构建与签名

使用 Xcode 工具链交叉编译为 iOS arm64 架构二进制：

```bash
# 1. 编译 Swift 可执行文件 (指定 iOS 15.0+ 目标与 library 解析模式)
xcrun -sdk iphoneos swiftc \
  -target arm64-apple-ios15.0 \
  -parse-as-library \
  -O \
  CGMReader.swift \
  -o CGMReader

# 2. 组装 .app 包结构
mkdir -p CGMReader.app
mv CGMReader CGMReader.app/
cp Info.plist CGMReader.app/

# 3. 替换 entitlements 中的 TEAMID 并签名
sed "s/TEAMID/<YOUR_TEAM_ID>/g" entitlements.plist > /tmp/entitlements.plist
ldid -S/tmp/entitlements.plist CGMReader.app/CGMReader
# 或整包签名
ldid -S/tmp/entitlements.plist CGMReader.app
```

---

## 安装与运行

### 1. 设备端安装 (越狱环境)

将 `CGMReader.app` 拷贝到设备端系统或越狱应用目录：

```bash
# 拷贝至越狱应用目录 (Dopamine / Rootless 环境)
scp -r CGMReader.app root@<IP>:/var/jb/Applications/

# 刷新桌面图标缓存
ssh root@<IP> "uicache -p /var/jb/Applications/CGMReader.app"
```

### 2. 运行方式

- **桌面点击**：通过 iOS 桌面图标启动前台运行。
- **命令行 / eco-launch 启动**：
  ```bash
  eco-launch com.sisensing.cgmreader
  ```

---

## 已知限制与注意事项

1. **传感器单连接互斥**：
   SiSensing ECO CGM 传感器仅支持单 BLE 连接。官方 App 与独立 CGMReader 互斥，同一时间只能由一个客户端持有连接。若官方 App 正在前台轮询，需先退出官方 App。
2. **锁屏状态下的广播发现抑制**：
   iOS 系统策略在锁屏状态下会抑制 SpringBoard 上下文的未过滤 BLE 扫描发现回调（DISCOVER 数量为 0）。端到端重新发现/扫描绑定建议在设备解锁状态下进行；一旦建立连接并订阅通知，后台传输可正常维持。
3. **免责声明**：
   本项目仅供个人数据互操作性与协议研究，非官方工具，不提供任何医疗准确性担保。
4. **日志隐私**：`ble_frames.log` 与事件日志包含运行时蓝牙标识（CBPeripheral UUID、广播名等）；对外分享日志前请自行脱敏。
5. **entitlements 模板**：`entitlements.plist` 为**仅越狱实验模板**（含私有 entitlement 与 `get-task-allow` 调试标志），普通开发者签名无法获得这些授权，不可用于常规安装/分发场景。
