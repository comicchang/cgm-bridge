# SiSensing ECO CGM BLE 协议规范 (Protocol Specification)

> **声明与免责**：本项目仅供个人数据互操作性与协议兼容性技术研究。非厂商关联，不代表官方规范。不提供厂商专有二进制/固件。请在合法拥有的设备上进行调试与数据读取。  
> **逆向基准**：SiSensing ECO iOS 3.9.4 (`ECO` Mach-O arm64)。  
> **置信等级标注**：  
> - `[fact]`：IDA 反汇编与代码常量直接证明的确定性事实。  
> - `[INFERENCE]`：基于数据结构、调用关系及上下文逻辑的严密推断。  
> - `[未验证]`：需真实设备抓包或动态测试确认的边缘情况。

---

## 1. BLE 基础连接配置 [fact]

| 参数项 | 值 / UUID | 证据来源 |
| :--- | :--- | :--- |
| **GATT Service UUID** | `0000FF30-0000-1000-8000-00805F9B34FB` (`FF30`) | `BleGS1V2ProductProcess.h` [fact] |
| **Write Characteristic** | `0000FF31-0000-1000-8000-00805F9B34FB` (`FF31`, Write without response / Write) | `BLEModel.h` [fact] |
| **Notify Characteristic** | `0000FF32-0000-1000-8000-00805F9B34FB` (`FF32`, Notify) | `BLEModel.h` [fact] |
| **Device Info Service** | `0000180A-0000-1000-8000-00805F9B34FB` (`180A`) | Standard BLE DIS [fact] |
| **Battery Service** | `0000180F-0000-1000-8000-00805F9B34FB` (`180F`) / 特征 `2A19` | Standard BLE BAS [fact] |

---

## 2. 帧格式与校验和算法

### 2.1 通用帧封装格式 [fact]

BLE 通信所有命令（发送与接收）均遵循统一的帧封装结构：

```
+------------+-----------+--------------------------+------------+
| Length (1B)| CmdId(1B) |     Payload (N Bytes)    | Checksum(1B|
+------------+-----------+--------------------------+------------+
| 0x00       | 0x01      | 0x02 ... (Length - 1)    | Length     |
```

- **Byte 0 (`Length`)** `[fact]`：帧长字段，数值等于 `1 + CmdId 长度 + Payload 长度`，即**不包含 Checksum 字节的总字节数**（整帧总字节数为 `Length + 1`）。
- **Byte 1 (`CmdId`)** `[fact]`：命令字枚举值。
- **Byte 2 .. `Length-1` (`Payload`)** `[fact]`：命令携带的参数，各多字节整型字段均为**小端序 (Little-Endian)**。
- **Byte `Length` (`Checksum`)** `[fact]`：累加和的补码校验字节。

### 2.2 Checksum 计算算法与验证向量 [fact]

#### 算法定义
根据底层通信函数 `sub_100B5C2B8` 与 `sub_100B5C738` 反汇编证明：
校验算法为单字节累加和补码（Two's Complement Additive Sum）：

$$Checksum = (- \sum_{i=0}^{Length-1} Byte_i) \pmod{256} = (256 - (\sum_{i=0}^{Length-1} Byte_i \pmod{256})) \pmod{256}$$

全帧合法性校验判据：
$$\sum_{i=0}^{Length} Byte_i \pmod{256} == 0$$

#### Python 参考实现
```python
def calc_checksum(frame_bytes_without_checksum: bytes) -> int:
    """计算帧尾 Checksum 字节"""
    s = sum(frame_bytes_without_checksum) & 0xFF
    return (-s) & 0xFF

def verify_frame(full_frame: bytes) -> bool:
    """验证包含 Checksum 的完整帧"""
    return (sum(full_frame) & 0xFF) == 0
```

#### 校验验证向量 (Test Vectors) [fact]
1. **Auth 帧 (明文模式)**:
   - MAC（小端逆序示例）: `AA BB CC DD EE FF`
- Token (16 字节 ASCII): `<AUTH_TOKEN>`
- 字节序列 (前 25 字节):
  `19 01 00 ff ee dd cc bb aa <16-byte AUTH_TOKEN>`
   - 累加和低字节与补码校验保证整包 `sum(frame) & 0xFF == 0`。

2. **StopDevice 帧 (type=1, stop directly)**:
   - 前 3 字节: `03 10 00`
   - Checksum: `(- (3 + 0x10 + 0)) & 0xFF = (-19) & 0xFF = 0xED`
   - 完整 4 字节帧: `03 10 00 ED`
   - 验证: `(3 + 16 + 0 + 237) & 0xFF == 0` ✓

3. **GetDeviceInfo 帧 (type=1)**:
   - 前 3 字节: `03 F0 01`
   - Checksum: `(- (3 + 0xF0 + 1)) & 0xFF = (-244) & 0xFF = 0x0C`
   - 完整 4 字节帧: `03 F0 01 0C`
   - 验证: `(3 + 240 + 1 + 12) & 0xFF == 0` ✓

4. **通用 5 字节 ACK 响应帧**:
   - 结构: `04 <cmdId> <result> <error_code> <checksum>`
   - 例: Auth 成功 ACK: `04 01 01 00 FA`
     - 校验: `(4 + 1 + 1 + 0 + 250) & 0xFF == 0` ✓

---

## 3. 命令集完整定义 (Command Table) [fact]

代码对应映射来源于 `cmdDataWithCmdId:sendCMDDataInfo:dataLength:isEncrypt:` 及其子构造函数：

| 序号 | 命令名称 | cmdId (Hex) | App 内部 cmdType | 构造函数 | 总帧长 (Bytes) | Length 字节 | 功能描述 |
| :---: | :--- | :---: | :---: | :--- | :---: | :---: | :--- |
| 1 | **sendAuthCMD** | `0x01` | 1 | `sub_100B5C738` | 26 | `0x19` (25) | 设备身份认证（含 MAC 与 App Token） |
| 2 | **sendAuthSwitch** | `0x02` | 2 | `sub_100B5C838` | 4 | `0x03` (3) | 切换认证模式（0=明文，1=密文） |
| 3 | **sendUpdateTimeInterval** | `0x03` | 4 | `sub_100B5C2B8` | 7 | `0x06` (6) | 更新设备时钟/基准时间戳 |
| 4 | **sendKeyValue** | `0x04` | 3 | `sub_100B5C8F8` | 20 | `0x13` (19) | 设置/下发自定义密钥 |
| 5 | **sendActivateCMD** | `0x07` | 5 | `sub_100B5C3AC` | 11 | `0x0A` (10) | 激活传感器（仅时间戳，灵敏度填 0） |
| 6 | **sendActivateArithmeticCMD** | `0x07` | 6 | `sub_100B5C3AC` | 11 | `0x0A` (10) | 算法模式激活（时间戳 + 解码后灵敏度整数） |
| 7 | **sendGetData** (v120) | `0x08` | 7 | `sub_100B5C4C4` | 7 | `0x06` (6) | 拉取标准历史血糖数据帧 |
| 8 | **sendGetDataArithmetic** | `0x0A` | 8 | `sub_100B5C9E0` | 7 | `0x06` (6) | 拉取算法压缩血糖数据帧 (GSM) |
| 9 | **sendGetDataThreePole** | `0x3E` | 10 | `sub_100B5CAD4` | 7 | `0x06` (6) | 拉取三电极原始电流与血糖数据帧 |
| 10 | **sendStopDeviceCMD** | `0x10` | 11 | `sub_100B5C5B8` | 4 | `0x03` (3) | 停止传感器工作 / 关机 |
| 11 | **sendGetDeviceInfoCMD** | `0xF0` | 12 | `sub_100B5C678` | 4 | `0x03` (3) | 查询设备系统信息（电量、状态等） |

---

## 4. 各命令发送 Payload 字节布局详解 [fact]

### 4.1 `sendAuthCMD` (cmdId = `0x01`)
- **总长度**：26 字节 (`Length = 0x19`)
- **函数**：`sub_100B5C738(isEncrypt, 0, &macReversed, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x19` (25) |
| 1 | 1B | u8 | `cmdId` | 固定 `0x01` |
| 2 | 1B | u8 | `subType` | 固定 `0x00` |
| 3..8 | 6B | 字节数组 | `macAddress` | **倒序小端** MAC 地址（MAC[5] 位于 Byte 3，MAC[0] 位于 Byte 8） |
| 9..24 | 16B | ASCII 字符串 | `secretToken` | 固化 App Token：`<AUTH_TOKEN>` (ASCII 16 字节) |
| 25 | 1B | u8 | `checksum` | 累加和补码 |

### 4.2 `sendAuthSwitch` (cmdId = `0x02`)
- **总长度**：4 字节 (`Length = 0x03`)
- **函数**：`sub_100B5C838(isEncrypt, type, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x03` (3) |
| 1 | 1B | u8 | `cmdId` | 固定 `0x02` |
| 2 | 1B | u8 | `type` | 模式：`0`=明文认证模式，`1`=密文认证模式 |
| 3 | 1B | u8 | `checksum` | `(- (5 + type)) & 0xFF` |

### 4.3 `sendUpdateTimeInterval` (cmdId = `0x03`)
- **总长度**：7 字节 (`Length = 0x06`)
- **函数**：`sub_100B5C2B8(isEncrypt, timeInterval, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x06` (6) |
| 1 | 1B | u8 | `cmdId` | 固定 `0x03` |
| 2..5 | 4B | u32 LE | `timeInterval` | 当前 UNIX 时间戳（秒级） |
| 6 | 1B | u8 | `checksum` | 累加和补码 |

### 4.4 `sendKeyValue` (cmdId = `0x04`)
- **总长度**：20 字节 (`Length = 0x13`)
- **函数**：`sub_100B5C8F8(isEncrypt, type, keyBytes, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x13` (19) |
| 1 | 1B | u8 | `cmdId` | 固定 `0x04` |
| 2 | 1B | u8 | `type` | 密钥类型索引（通常传 1） |
| 3..18 | 16B | 原始字节 | `keyData` | 16 字节密钥数据 |
| 19 | 1B | u8 | `checksum` | 累加和补码 |

### 4.5 `sendActivateCMD` / `sendActivateArithmeticCMD` (cmdId = `0x07`)
- **总长度**：11 字节 (`Length = 0x0A`)
- **函数**：`sub_100B5C3AC(isEncrypt, timeInterval, sensitivity, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x0A` (10) |
| 1 | 1B | u8 | `cmdId` | 固定 `0x07` |
| 2..5 | 4B | u32 LE | `timeInterval` | 激活基准 UNIX 时间戳（秒级） |
| 6..9 | 4B | u32 LE | `sensitivity` | 灵敏度整型值：无算法传 `0`；算法模式传解码后的灵敏度整数（如 `1620`） |
| 10 | 1B | u8 | `checksum` | 累加和补码 |

### 4.6 `sendGetData` (cmdId = `0x08`, `0x0A`, `0x3E`)
- **总长度**：7 字节 (`Length = 0x06`)
- **函数**：
  - 标准模式 (cmd 0x08): `sub_100B5C4C4(isEncrypt, startIndex, 0, buf, 30)`
  - 算法模式 (cmd 0x0A): `sub_100B5C9E0(isEncrypt, startIndex, 0, buf, 30)`
  - 三电极模式 (cmd 0x3E): `sub_100B5CAD4(isEncrypt, startIndex, 0, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x06` (6) |
| 1 | 1B | u8 | `cmdId` | `0x08` (标准) / `0x0A` (GSM) / `0x3E` (三电极) |
| 2..3 | 2B | u16 LE | `startIndex` | 请求的起始历史记录序号（从 1 开始累加；0 表示未初始化） |
| 4..5 | 2B | u16 LE | `indexNumber` | 请求数量，App 默认填 `0x0000`（由传感器按自身最大包长连续推送） |
| 6 | 1B | u8 | `checksum` | 累加和补码 |

### 4.7 `sendStopDeviceCMD` (cmdId = `0x10`)
- **总长度**：4 字节 (`Length = 0x03`)
- **函数**：`sub_100B5C5B8(isEncrypt, isType2, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x03` (3) |
| 1 | 1B | u8 | `cmdId` | 固定 `0x10` |
| 2 | 1B | u8 | `type` | 停止类型：`0x00` (普通停止), `0x01` (关机/重置) |
| 3 | 1B | u8 | `checksum` | `(- (19 + type)) & 0xFF` |

### 4.8 `sendGetDeviceInfoCMD` (cmdId = `0xF0`)
- **总长度**：4 字节 (`Length = 0x03`)
- **函数**：`sub_100B5C678(isEncrypt, type, buf, 30)`

| 偏移 | 长度 | 类型 | 字段 | 含义说明 |
| :---: | :---: | :---: | :--- | :--- |
| 0 | 1B | u8 | `length` | 固定 `0x03` (3) |
| 1 | 1B | u8 | `cmdId` | 固定 `0xF0` |
| 2 | 1B | u8 | `type` | 查询类型：`1`=灵敏度, `2`=激活状态, `3`=时钟, `4`=存储信息, `5`=当前电流/温度 |
| 3 | 1B | u8 | `checksum` | `(13 - type) & 0xFF` |

---

## 5. 加密体系与算法路径 (RC4 Implementation) [fact]

### 5.1 加密算法与密钥推导
- **算法核心** `[fact]`：标准 **RC4 (Rivest Cipher 4 / ARC4)** 流加密算法。
  - 函数 `sub_100B52D20`：标准 256 字节 S-Box 初始化与 KSA 密钥调度。
  - 函数 `sub_100B52DD4`：标准 PRGA 伪随机生成与异或加密。
  - Drop 字节数：`0`（不丢弃前导字节）。
- **静态 Master Key (`unk_100D01FFC`)** `[fact]`：固化在二进制数据段的 16 字节：
  ```
  <MASTER_KEY>
  ```
- **AppKey 与 App Token 提取链** `[fact]`：
  1. App 启动时由解密函数输出 90 字符十六进制 AppKey：
     `<APP_KEY>`
  2. `sub_100B5EC6C` 将其转为 45 字节，使用静态 Master Key 执行 RC4 解密。
  3. 解密结果偏移 6..21（16 字节 ASCII）即为用于 `sendAuthCMD` 的固定 `secretToken`：`<AUTH_TOKEN>`。
  4. 解密结果偏移 22..38 校验 bundleIdentifier (`com.sisensing.eco`)。

### 5.2 帧加密范围与处理流程 `[fact]`
- **发送端**：
  1. 组装明文帧：Byte 0 (`Length`) + Byte 1 (`cmdId`) + Payload。
  2. 计算 Checksum 字节并附加在末尾。
  3. 若启用加密 (`isEncrypt == 1`)：将包含 Length、CmdId、Payload、Checksum 的**全部 `Length + 1` 个字节整帧作为输入，使用 RC4 和 Master Key 原地异或加密**。
  4. 写入 Characteristic `FF31`。
- **接收端**：
  1. 接收 `FF32` 通知的整包。
  2. 若启用加密 (`isEncrypt == 1`) 且非未加密 5 字节错误帧 (`04 00 00 00 FC`)：整包经 RC4 解密还原为明文。
  3. 校验末尾 Checksum；验证通过后按命令类型解析。

---

## 6. 响应包解析与数据帧布局 (Notify FF32) [fact]

### 6.1 通用 ACK 响应结构 [fact]
当发送 `auth`, `activate`, `stopDevice`, `updateTimeInterval` 等命令时，传感器返回 5 字节应答包：
- **总长度**：5 字节
- **明文结构**：
  - `Byte 0`：`0x04` (`Length = 4`)
  - `Byte 1`：`cmdId` (被应答的命令字，如 0x01)
  - `Byte 2`：`result` (`0x01`=成功，`0x00`=失败)
  - `Byte 3`：`error_code` (`0x00`=无错误；非 0 为错误码)
  - `Byte 4`：`checksum`

### 6.2 历史血糖数据帧 (cmdId = `0x08`, Version 120 标准格式) [fact]
发送 `sendGetData` (cmd 0x08) 后，传感器在 `FF32` 连续推送数据包。每个 BLE 包可携带多条 1 分钟点（Records），解密后的布局如下：

#### 包头 (Header, 9 字节) [fact]
- `Byte 0` (1B): `Length` (整个包不含 checksum 的字节数)
- `Byte 1` (1B): `cmdId` = `0x08`
- `Byte 2` (1B): `pNum` = 本包中包含的血糖点记录数（通常 1~20 条）
- `Byte 3..4` (2B, u16 LE): `startIndex` = 本包第一条记录的绝对序号
- `Byte 5..8` (4B, u32 LE): `startTime` = 本包第一条记录的基准时间戳（秒级 UNIX 时间）

#### 记录区 (Records, 每条记录固定 8 字节) [fact]
从 Byte 9 开始，每 8 字节为一条记录 $R_k$（$k = 0, \dots, pNum - 1$）：
- **绝对序号**：$Index_k = startIndex + k$
- **绝对时间戳**：$Time_k = startTime + k \times 60$（**每点严格间隔 60 秒**）
- **8 字节记录字段映射**（相对记录起点偏移 0..7）：
  - **Offset +0 .. +1 (2B, u16 LE)**：`tempRaw` = 温度原始值。
    - 换算公式：$Temperature (^\circ\text{C}) = \frac{tempRaw}{10.0}$
  - **Offset +2 .. +3 (2B, u16 LE)**：`dumpRaw` = 电池电量 / 硬件状态。
  - **Offset +4 .. +5 (2B, u16 LE)**：`currentRaw` = 传感器工作电极原始电流。
    - 换算公式：$Current (\text{nA 或计数}) = \frac{currentRaw}{10.0}$
  - **Offset +6 (1B, u8)**：状态标志与血糖低位：
    - `bit 0`：`twarn` / `cwarn`（温度告警 / 电流告警标志）
    - `bits 1..2`：`gwarn`（血糖告警状态）
    - `bits 3..5`：`trend`（趋势箭头：0=平稳, 1=缓升, 2=急升等）
    - `bits 6..7`：`glouse[0:1]`（10 位血糖值的最低 2 位）
  - **Offset +7 (1B, u8)**：`glouse[2:9]`（10 位血糖值的高 8 位）。

#### 血糖值换算公式 [fact]
$$glouseRaw = (\text{byte6} \gg 6) \mid (\text{byte7} \ll 2) \quad (10\text{位整数，范围 } 0 \sim 1023)$$
$$Glucose (\text{mmol/L}) = \frac{glouseRaw}{10.0}$$

#### 包尾 (Tail, 3 字节) [fact]
- `Byte [Length-2 .. Length-1]` (2B, u16 LE): `lidx` / `reindex` = **传感器中尚未发送的剩余历史记录条数**。
  - 当 `lidx == 0` 时，表明传感器内部历史缓存已全部排空同步完毕。
- `Byte Length` (1B, u8): `checksum`。

---

## 7. 灵敏度解码算法 (Sensitivity Decoding) [fact]

设备铭牌/包装条码解析出的 4 位短码（如 `<SENS_DECODE_4CHAR>`）转换为下发给传感器的整型灵敏度：

### 算法流程 [fact] (`sub_100B5EFF0`)
1. 输入 4 位字符，补齐为 8 字符 `"0000" + code`。
2. 在固化密码表（含 `999J,777I,...` 等条目）中匹配与变换。
3. 执行反向字符差分：
   - 设基准字符 $v_4 = 65$ (`'A'`)。
   - 对前 3 字符计算解码数值，第 4 字符校验模 10 累加和。
4. 得到浮点灵敏度：`strtoul(__str, 10) / 100.0`。
   - 示例：若计算得到 `1.62` mmol/L。
5. 在 `sendActivateArithmeticCMD` 中将浮点值乘以 1000 转为整型：
   $$\text{sensitivity\_param} = \text{int}(1.62 \times 1000) = 1620 \quad (\text{0x00000654})$$

---

## 8. 独立客户端交互时序

```
[Client]                                                        [ECO Sensor]
   |                                                                  |
   |--- 1. BLE Scan & Connect (Filter by Name "<BLE_NAME>" / FF30) -->|
   |<-- 2. Connected; Discover Services FF30 & Characteristics -------|
   |--- 3. Subscribe Notify on Characteristic FF32 ------------------>|
   |                                                                  |
   |--- 4. Send Auth (cmd 0x01, Plaintext): ------------------------->|
   |       Payload: Length=0x19, MAC(LE), Token="<AUTH_TOKEN>"        |
   |<-- 5. Receive Auth ACK: 04 01 01 00 FA (Success!) ---------------|
   |                                                                  |
   |--- 6. (Optional) Send AuthSwitch (cmd 0x02) to toggle RC4 ------>|
   |<-- 7. Receive AuthSwitch ACK ------------------------------------|
   |                                                                  |
   |--- 8. Send Activate (cmd 0x07): -------------------------------->|
   |       Payload: TimeInterval(u32 LE)=Now, Sensitivity=<param>     |
   |<-- 9. Receive Activate ACK: 04 07 01 00 F4 (Success!) -----------|
   |                                                                  |
   |--- 10. Send GetData (cmd 0x08): -------------------------------->|
   |        Payload: startIndex=1, indexNumber=0                      |
   |<-- 11. Stream Data Notify Packets (cmd 0x08): -------------------|
   |        [Frame 1]: pNum=20, startIndex=1, itime=T0, ..., lidx=120 |
   |        [Frame 2]: pNum=20, startIndex=21, itime=T20, ..., lidx=100|
   |        ...                                                       |
   |        [Frame N]: pNum=15, startIndex=126, ..., lidx=0 (End!)    |
   |                                                                  |
   |--- 12. Parse, Save to Local DB, & Sync to Apple HealthKit -------|
   |--- 13. (Optional) Send StopDevice (cmd 0x10) or Disconnect ----->|
```

---

## 9. 待抓包验证项清单 (Verification Checklist)

虽然逆向已完全覆盖代码逻辑，但在真机调试时仍需通过抓包核对以下 3 项边界：

1. **[未验证] 传感器出厂默认是否开启 RC4 密文**：
   - 厂商 App 在首次连接时先发明文 Auth（`isEncrypt=0`）；若设备回复未加密错误或拒绝，App 会通过 `sendAuthSwitch` 切换。独立客户端应首先尝试明文 `isEncrypt=0` 发送 Auth。
2. **[未验证] 激活命令是否必须每次重连发送**：
   - 如果传感器已在体运行中，重新连上 BLE 后是直接发送 `sendGetData` 即可拉取数据，还是必须先发送一次 `sendActivate` 刷新时钟基准。
3. **[未验证] 单次拉取最大包记录数 (pNum)**：
   - 在真实 BLE 协商 MTU（如 247 字节）下，单包 `pNum` 的实际返回上限通常为 20~25 条记录。
