# SiSensing ECO 算法转换与架构备忘录 (Algorithm Notes)

> **免责与合规声明**：  
> 本技术备忘录探讨传感器原始采样信号（RAW 电流/ADC）与血糖物理量（mmol/L）转换的数学与工程实现。本项目不提供修改后的医疗算法，亦不分发厂商专有算法二进制；所有方案仅作为个人学术逆向工程与技术可行性记录。

---

## 1. 为什么 RAW 信号转物理血糖必须依赖原生算法？

在 SiSensing ECO CGM 系统中，传感器固件在 BLE 通道传输的数据帧内虽然包含直接经初级缩放的 `glouseRaw` 字段，但真正的临床参考血糖并非简单的常数乘除，而是涉及复杂的物理与生物电化学滤波 [fact]：

### 1.1 原始数据流特征 [fact]
历史数据包（cmd `0x08`）中包含：
- `currentRaw`: 工作电极微弱原始电流（Nanoamperes, nA 量纲或采样计数值）。
- `tempRaw`: 皮肤传感器探头表面温度（$0.1^\circ\text{C}$ 精度）。
- `glouseRaw`: 10 位初级血糖字（范围 $0 \sim 1023$）。

### 1.2 复杂生物滤波与漂移补偿
经对官方二进制 `ECO` 中 Native 算法库 `NativeAlgorithmVE1_1_5N`（符号常量 `"ALGORITHM E1.1.5N(2025_09_02)"`）深入逆向发现：
1. **连续滑动统计与趋势拟合**：核心滤波函数（`sub_100AE8F0C`，代码段长达 103 KB）维护了最近 30 分钟的多采样滑动窗口，执行多项式加权拟合与高阶卡尔曼滤波 [fact]。
2. **探头生物活性衰减补偿**：葡萄糖氧化酶在人体皮下组织中存在随天数递减的灵敏度衰减曲线与基线漂移，需结合累计时间与皮肤温度积分动态补偿 [INFERENCE]。
3. **温度系数校正**：根据实时温度偏离基线计算修正项，防止体温波动引起虚假血糖尖峰。
4. **双限报警迟滞状态机**：维护告警防抖与迟滞状态，避免在阈值临界点频繁跳动。

因此，单纯读取 BLE 包中的整数无法复现厂商算法输出的平滑读数；直接调用或复用官方 Native 算法引擎是保证与官方客户端计算同构的最可靠路径 [INFERENCE]。

---

## 2. 2,456 字节 `binaryAlgorithmContext` 状态上下文

算法在运行期不是无状态的纯函数，而是重度依赖一个尺寸严格为 **2,456 字节**（十六进制 `0x998`）的二进制状态快照 `binaryAlgorithmContext` [fact]。

### 2.1 结构体尺寸与物理验证 [fact]
- `sub_100ADF1AC`（构造函数）：调用 `calloc(1, 0x998)` 分配。
- `sub_100ADF3BC` (`getStructSize`)：硬编码返回常数 `2456`。
- `sub_100ADF3C4` (`setBinaryStructAlgorithmContext:`)：执行 `memcpy(ctx, src, 0x998)`。
- 本地落盘文件 `lastDevice-binaryAlgorithmContext.bin` 及数据库 BLOB 均为严格 2,456 字节。

### 2.2 核心关键字段内部布局 [fact] / [INFERENCE]

```c
#pragma pack(push, 1)
struct BinaryAlgorithmContextVE115N {
    /* 0x000 (0)   */ uint64_t magic_header;         // 初始 0x100000001ULL (状态标志/版本魔数) [fact]
    /* 0x008 (8)   */ char     version_str[30];       // "ALGORITHM E1.1.5N(2025_09_02)\0" [fact]
    /* 0x026 (38)  */ uint16_t reserved_word;        // 0 [fact]
    /* 0x028 (40)  */ float    recent_glucose;        // 最近一次有效血糖值 (mmol/L) [INFERENCE]
    /* 0x02C (44)  */ float    filtered_glucose;      // 平滑滤波后血糖值 [INFERENCE]
    /* 0x030 (48)  */ uint32_t sample_index_last;     // 上一次处理的 index [INFERENCE]
    /* 0x034 (52)  */ uint32_t glucose_warning;       // 血糖报警状态 [fact]
    /* 0x038 (56)  */ uint32_t current_warning;       // 电流报警状态 [fact]
    /* 0x03C (60)  */ uint32_t temp_warning;          // 温度报警状态 [fact]
    /* 0x040 (64)  */ uint32_t trend_arrow;           // 血糖趋势箭头 (0-5) [fact]
    /* 0x0E0 (224) */ float    sensitivity_factor;    // 灵敏度校准因子 (sub_100AE63C8 计算结果) [fact]
    /* 0x0E4 (228) */ uint8_t  decay_params[120];     // 敏感度衰减曲线与基线漂移参数 [INFERENCE]
    /* 0x15C (348) */ float    temp_baseline;         // 温度基准补偿初值 [INFERENCE]
    /* 0x2A0 (672) */ uint8_t  kalman_engine[656];    // 卡尔曼/高阶滤波状态区 [fact]
    /* 0x4C0 (1216)*/ float    effective_sens;        // 当前生效敏感度 [fact]
    /* 0x594 (1428)*/ uint8_t  state_history[684];    // 连续采样差分与滞后队列 [INFERENCE]
    /* 0x848 (2120)*/ uint8_t  warning_hysteresis[328];// 双限报警滞后与消抖缓存 [INFERENCE]
    /* 0x990 (2448)*/ uint64_t sensor_runtime_meta;   // 累积运行时步 [fact]
}; // 总大小正好为 2456 字节 (0x998)
#pragma pack(pop)
```

---

## 3. 算法抽取方案评估与方案乙（整二进制转 dylib）

要让独立客户端使用该原生算法，对比了三种实现路径：

| 评估维度 | 方案甲：函数提取与重新链接 | 方案乙：整二进制转 dylib + dlopen (推荐) | 方案丙：纯代码反向重写 |
| :--- | :--- | :--- | :--- |
| **技术机制** | 裁剪 `__TEXT` 目标函数，重建符号与重定位表链接新 dylib | Mach-O 头部修补 (`MH_EXECUTE`→`MH_DYLIB`)，清零 `__PAGEZERO`，无害化入口与反调试，`dlopen` 加载 | 从反汇编 140+ KB 汇编中完全逆向手写 Swift/C++ |
| **可行性判定** | **不可行 / 极低** [fact] | **结构层面可行（推荐路线）**——基于 Mach-O 结构分析的论证，整链路未实测 [INFERENCE] | **高风险 / 不推荐** [INFERENCE] |
| **开发工作量** | 2~3 周（静态无重定位表，极难修复） | 估 1~2 天（估算值，未实测） | 数人月（非线性滤波反推极度繁琐） |
| **精度一致性** | 100%（若能链接） | 原生代码原样加载，**理论上**数值路径与官方一致；未实测验证 [INFERENCE] | 易产生累积发散与数值偏差 |
| **环境兼容性** | 标准 App | 目标环境为越狱设备（dlopen 注入） | 跨平台通用 |

> **状态**：方案乙为 v2 规划路线，当前仓库未交付实现；上表为设计阶段评估，非实测结论。

### 3.1 方案甲为何不可行？[fact]
1. iOS 商业二进制在发布时已完全剥离内部重定位表（`relocs stripped`）。ARM64 采用基于 PC 的相对寻址（`ADRP` + `ADD`/`LDR`），立即数硬编码了相对于当前代码页的固定虚拟偏移。
2. 算法深层读取了 `__TEXT.__const` 中 254 处只读浮点常数矩阵及 `__data` 中静态置换查找表。任意代码抽离都会使地址映射崩塌，引发内存段访问错误崩溃。

### 3.2 方案乙实施核心技术细节 [fact]
将原可执行文件转换为 dylib，整块保留相对页面偏移：
1. **Mach-O Header 修改**：
   - 将 offset `0x0C` 的 `filetype` 从 `MH_EXECUTE` (`2`) 改为 `MH_DYLIB` (`6`)。
2. **清零 `__PAGEZERO`**：
   - 定位 `LC_SEGMENT_64 (__PAGEZERO)`，将 `vmsize` 置为 `0`，`initprot` 与 `maxprot` 置为 `0`（dyld 加载 dylib 时禁止映射 4GB 页面保留区）。
3. **替换 Load Commands**：
   - 将 `LC_LOAD_DYLINKER` 转换为 `LC_ID_DYLIB`，指定 `@rpath/ECO.dylib`。
   - 将 `LC_MAIN` 替换为无害类型（如 `LC_RPATH`），防止入口冲突。
4. **反调试自毁函数无害化**：
   - 二进制中的 `InitFunc_0` (`0x100317E44`) 与 `InitFunc_1` (`0x100318E3C`) 会在构造时检测环境并执行 `longjmp`。
   - 补丁：将这两个函数的首指令修改为 ARM64 `RET` (`0xC0 0x03 0x5F 0xD6`)。
5. **重签名**：执行 `ldid -S` 或使用开发者证书签名。

---

## 4. 实施潜在风险与缓解策略

1. **宿主 App 框架动态依赖** [fact]：
   - 该二进制依赖 `@rpath` 下的 `Charts.framework`、`ImSDK_Plus.framework`、`ScanKitFrameWork.framework`。
   - 缓解策略：独立客户端工程需将这些 framework 复制至自身 `Frameworks/` 目录，或在打包时采用 weak-linking 形式满足 dyld 符号链接。
2. **上下文持久化与分叉竞争** [INFERENCE]：
   - 算法依赖 2,456 字节 context 在每次处理后回写。若独立读取器与官方 App 同时尝试交替更新状态，上下文内步数和卡尔曼方差将发生分叉。
   - 缓解策略：独立客户端在本地维护专属的 context 存储文件；传感器是单连接设备，原则上由独立客户端单一维护连接与步进。
