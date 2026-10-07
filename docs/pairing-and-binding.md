# SiSensing ECO CGM 配对与绑定机制 (Pairing and Binding)

> **免责与合规声明**：  
> 本文档旨在探讨个人医疗设备互操作性、数据主权与独立客户端数据拉取协议。研究内容不涉及破解厂商商业机密，不鼓励亦不提供商业化作弊方案。所有分析材料均源自用户自身合法拥有的硬件与客户端本地副本逆向分析。

---

## 1. 配对完整流程逆向事实 [fact]

SiSensing ECO CGM 传感器的接入与激活分为四个核心阶段：扫码解析、灵敏度与短码派生、BLE 鉴权握手，以及激活命令下发。

```
+---------------+     +-----------------------+     +-----------------------+     +----------------------+
| 1. 扫描包装码  | --> | 2. 14位 sensitivityAll | --> | 3. 码表派生 4位短码    | --> | 4. BLE 鉴权与激活     |
| (SN / QR Code)|     |    (linkCode)         |     |    (sensitivityDecode)|     |    (Auth -> Activate)|
+---------------+     +-----------------------+     +-----------------------+     +----------------------+
```

### 1.1 扫码与参数输入 (`ScanQRCode` / `CodeViewModel`) [fact]
- 用户扫描包装盒或托盘上的二维码后，App 读取并解析传感器序列号（SN，14 位大写字母与数字组合，即 `sensitivityAll` 或 `linkCode`）。
- 伴随提取的还有 `batchnum`（生产批次号）与设备型号标示。

### 1.2 灵敏度解码与 4 位短码派生 [fact]
App 侧由 `initEcoEncryptAlgorithmWithlinkCode:batchnum:terminal_num:serialnum:sensitivity:` 调用底层 native 函数：
1. `sub_100B1BCC4(linkCode, ..., &v15)` 对 14 位 `linkCode` 解码，计算得到整型灵敏度参数（例如 `v15 = 162`，对应浮点灵敏度 `1.62` mmol/L/U）[fact]。
2. `snprintf("%04d", 10 * v15)` 格式化为 4 位字符串。
3. `sub_100B1C2C0` 执行字母变体编码并匹配码表常数表（包含 1000 个形如 `999J,777I,...,CCCC` 条目），派生出 **4 位配对短码**（`sensitivityDecode`，例如 `<SENS_DECODE_4CHAR>`）[fact]。
4. 截取蓝牙名称后 4 位作为 `blueNumber`。

### 1.3 BLE 扫描与握手认证 (`sendAuthCMD`) [fact]
1. App 使用 CoreBluetooth 扫描包含 Service `FF30`（或名称匹配 `<BLE_NAME>`）的设备。
2. 发现并连接后，发现 Service `FF30`、写入特征 `FF31` 及通知特征 `FF32`，立即订阅 `FF32`。
3. 构造 26 字节 Auth 帧（cmdId = `0x01`）：
   - `Byte 0`: `0x19` (25)
   - `Byte 1`: `0x01`
   - `Byte 2`: `0x00`
   - `Byte 3..8`: 目标设备 MAC 地址（小端逆序字节序）
   - `Byte 9..24`: 固化 App Token `<AUTH_TOKEN>` (16 字节 ASCII)
   - `Byte 25`: 单字节补码累加和 Checksum
4. 传感器通过 `FF32` 返回 5 字节 ACK：`04 01 01 00 FA`（Byte 2 为 `0x01` 表示鉴权通过）。

### 1.4 传感器激活与时钟同步 (`sendActivateArithmeticCMD`) [fact]
1. 构造 11 字节激活帧（cmdId = `0x07`）：
   - `Byte 0`: `0x0A` (10)
   - `Byte 1`: `0x07`
   - `Byte 2..5`: 当前客户端基准秒级 UNIX 时间戳 (u32 LE)
   - `Byte 6..9`: 整型灵敏度参数（如 `1620`，对应 `1.62 * 1000`）
   - `Byte 10`: Checksum
2. 传感器收到后返回 5 字节 ACK：`04 07 01 00 F4`，传感器正式进入运行与历史采样记录阶段。

---

## 2. 常见配对失败原因与排查矩阵

在独立客户端接入或二次连接时，配对与握手失败通常由以下原因引起：

| 现象 / 错误原因 | 逆向底层机制分析 | 置信度 | 应对与排查措施 |
| :--- | :--- | :---: | :--- |
| **短码/灵敏度错误** (`Failed to decrypt sensitivity`) | 激活时下发的灵敏度整数与传感器固件内部预设不匹配；或短码不满足 4 位字母/数字规范。 | [fact] | 确认 14 位 SN 解析正确；复用官方 App 首次成功绑定后落盘在本地的 4 位 `sensitivityDecode`。 |
| **官方 App / 宿主单连接占用** (扫描不到或连接超时) | BLE 传感器硬件设计为**单主设备连接 (Single Central Connection)**。当官方 App 在前台或挂在后台维护连接时，传感器停止向外广播或拒绝第二个 Central 的连接请求。 | [fact] | 彻底划掉/杀死官方 App，或临时关闭宿主设备蓝牙 10 秒后重试。 |
| **明文 ↔ RC4 密文模式不匹配** (传感器无 ACK 或回复错误码) | 官方 App 具备双模式自适应：默认先发明文 Auth，失败时发 `sendAuthSwitch` (cmd `0x02`, type `1`) 开启 RC4 流加密。若客户端状态机与传感器当前状态不同步，通信解包失败。 | [fact] | 客户端握手逻辑应实现自适应重试：明文认证超时后，自动重试带 RC4 加密的 Auth 握手。 |
| **到期重连门禁硬拦截** (`deviceStatus = '2'`) | 官方 App 本地数据库或 `lastDevice.plist` 中，当累计点数到达上限时，`reConnectWithDB` 与 `reConnectWithNoneCallback` 均有 `if (deviceStatus == 2) return;` 硬拦截，完全不触发扫描。 | [fact] | 独立客户端不受此代码约束，应直接发起扫描；若使用打补丁官方 App，需本地将状态改回 `'1'`。 |

---

## 3. 独立客户端如何合法复用自己设备的配对材料

独立客户端（如开源独立读取器、家庭自动化集成）要在无需逆向云端 API 的情况下直接直连传感器，最稳健的方法是**复用用户已绑定设备的本地配置材料**：

### 3.1 提取材料清单
用户在官方 App 首次扫码绑定传感器并完成激活后，客户端沙盒本地已沉淀了完整免密连接参数：
1. **设备蓝牙广播名 (`bleName`)**：形如 `<BLE_NAME>`，后 4 位为配对短码前缀。
2. **设备 MAC 地址 (`macAddress`)**：6 字节物理地址（iOS 上 CoreBluetooth 不直接暴露，但官方 App 数据库与本地 plist 记录了真实 MAC）。
3. **已解码灵敏度 (`sensitivityDecode`)**：4 位字符串（如 `<SENS_DECODE_4CHAR>`），或换算后的整型灵敏度 `1620`。
4. **传感器序列号 (`sensitivityAll` / `st`)**：14 位 SN。

### 3.2 提取源路径（在越狱或备份环境下）
- **本地属性列表**：`Library/Preferences/com.sisensing.eco.plist` 或沙盒 `Documents/lastDevice.plist`。
- **本地 SQLite 数据库**：`Documents/glucoseData.db`，查询表 `BlueDeviceModel`：
  ```sql
  SELECT bleName, macAddress, sensitivityDecode, sensitivityAll, activeTime
  FROM BlueDeviceModel
  ORDER BY activeTime DESC LIMIT 1;
  ```

### 3.3 独立客户端配置复用步骤
1. 将提取出的 `bleName`、`macAddress`、`sensitivityDecode` 填入独立客户端的本地配置文件或作为启动参数传入。
2. 独立客户端启动 `CBCentralManager`：
   - 使用名称过滤或 `retrieveConnectedPeripheralsWithServices:` 检索 `FF30` 服务。
3. 建立连接后，按照 §1.3 规范，使用上述 MAC 地址拼装并发送 26 字节 Auth 帧。
4. 鉴权通过后直接轮询或发送 `sendGetData` (cmd `0x08`) 连续拉取历史数据帧，实现数据完全本地离线导出。
