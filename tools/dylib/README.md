# ECOStatusMask — 运行时状态掩码动态库（dylib）

## 用途

官方客户端（`com.sisensing.eco`）内置一个**可自我复活的探头失效门禁**：当传感器上报异常（数据状态位 `ast='2'`）时，客户端写入 `deviceStatus='4'`（探头失效态），随后：

1. **硬阻断 BLE 重连扫描**——重连门禁对 `deviceStatus == '2'` 与 `'4'` 均直接拦截，App 根本不发起扫描（表现为"自动连接未发现设备"）；
2. **丢弃所有后续数据包**——数据处理入口对 `'4'` 直接返回。

即使手动把数据库/plist 中的状态复位为 `'1'`，客户端在下次启动校验/记录同步时仍会重新写回 `'4'`（门禁自我复活）。本 dylib 在**运行时**掩蔽该状态，使数据管线保持连通。

## 掩蔽面（最小化设计）

| 目标 | 选择子 | 掩蔽行为 |
|---|---|---|
| `BlueDeviceModel` | `deviceStatus`（getter） | 读到 `'2'`/`'4'` 时返回 `'1'`——覆盖重连门禁的读取点 |
| `BlueDeviceModel` | `setDeviceStatus:` | 入参 `'2'`/`'4'` 改写为 `'1'` 后透传 |
| `GlucoseModel` | `deviceStatus` / `setDeviceStatus:` | 同上（数据处理入口的读取点） |
| `BlueDataDispose` | `saveAlarmStatusAndDeviceStatusWithGlucoseModel:alarmStatus:deviceStatus:` | 第 3 参掩码，`alarmStatus` 透传保持厂商语义 |

**ABI 依据**（对目标版本 3.9.4 二进制 method encoding 的逆向核对；其他 App 版本需重新核对）：getter `@16@0:8`（返回 `NSString *`）、setter `v24@0:8@16`（void，单对象参数）。[fact@3.9.4]

**安装语义（原子性）**：constructor 首次尝试安装，失败后 50ms / 200ms 各重试一次。安装分两阶段：先对三个类与全部五个选择子做**全量预检（零副作用）**，任一缺失（如 App 版本变化导致改名）即判定失败——**不执行任何 IMP 交换，无部分安装状态**；预检全部通过后才一次性完成全部 hook。以系统日志中 `StatusMask: precheck failed …` / `install attempt=N ok=M` 核对安装结果。

## 与二进制补丁（tools/ios/）的分工

- **二进制补丁 = 权威层**：寿命常量、BLE 包闸、第三方崩溃修复——静态、持久、可审计。
- **本 dylib = 缺口层**：仅处理二进制补丁不覆盖的运行时状态门禁。二者互补，不重复。

## 构建

```bash
xcrun -sdk iphoneos clang -arch arm64 -miphoneos-version-min=15.0 \
  -fno-objc-arc -dynamiclib -framework Foundation \
  ECOStatusMask.m -o ECOStatusMask.dylib
ldid -S ECOStatusMask.dylib   # 越狱环境 ad-hoc 签名即可
```

## 部署（越狱设备）

```bash
scp ECOStatusMask.dylib ECOStatusMask.plist mobile@<device>:/var/mobile/Documents/
# 设备侧（root）：
cp /var/mobile/Documents/ECOStatusMask.{dylib,plist} \
   /var/jb/Library/MobileSubstrate/DynamicLibraries/
chown mobile:mobile /var/jb/Library/MobileSubstrate/DynamicLibraries/ECOStatusMask.*
```

- Filter 精确匹配 `com.sisensing.eco`，仅注入目标 App 进程，**无需 respring**；重启 App 即生效。
- **不触碰 App 数据容器**——无容器重绑定、无登录态丢失风险。

## 回滚

删除 `/var/jb/Library/MobileSubstrate/DynamicLibraries/ECOStatusMask.{dylib,plist}` 后重启 App，即恢复原生行为。本 dylib 只做运行时方法交换，不修改 App 二进制与数据库文件，回滚无残留。

## 已知限制

1. **存在绕过 setter 的直写路径**：实测中数据库内的 `deviceStatus` 仍可能出现 `'4'` 残留（客户端某条记录同步/ORM 直写路径不经过被 hook 的 setter）。由于运行时行为由 getter 掩蔽控制，数据管线不受影响——这只是存储层的显示残留。[fact]
2. 本 dylib **不恢复传感器物理精度**：它只保证数据管线连通；传感器寿命末期的精度衰减与异常态标记（见 [docs/data-quality.md](../../docs/data-quality.md)）不受任何软件措施影响。
3. 仅在越狱设备上可用（Substrate/ElleKit 注入机制）。
