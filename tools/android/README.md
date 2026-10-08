# Android 官方 APK 补丁与重签名工具

本目录提供针对 SiSensing ECO Android 官方应用的补丁与自动化重签名工具，用于个人持续葡萄糖监测（CGM）数据互操作研究与传感器 24 天生命周期扩展。

> **免责声明**：
> 本工具仅供个人研究与数据互操作探索，不与任何商业公司关联。
> 本工具不分发、不附带任何官方 APK 安装包、DEX 字节码或 Native 动态库。
> 输入的 APK 文件必须由使用者从自身合法拥有的设备副本提取。
> 修改官方应用可能违反用户协议或影响厂商精度认证，相关风险由使用者自行承担。

---

## 1. 目录结构

```text
tools/android/
├── patch_official_apk.py       # 自动化反编译、门禁核验、Smali 替换、回编译、重签名脚本
├── patch_config.example.json   # 补丁站点配置示例模板（支持 02.26.x 与 3.9.x 基线）
└── README.md                   # 本说明文档
```

---

## 2. 依赖工具

脚本依赖标准 Android 逆向与编译工具链：
- **Python 3.8+**
- **apktool**：用于 APK 解包与 Smali 还原（`brew install apktool`）
- **zipalign**：用于 APK 4 字节边界对齐（Android SDK `build-tools` 内置）
- **apksigner**：用于 APK v1/v2 签名生成与校验（Android SDK `build-tools` 内置）
- **keytool**：JDK 自带，用于首次自动生成调试证书（`debug.keystore`）

> 脚本会自动搜索系统 `PATH`、Homebrew 安装路径以及 `~/Library/Android/sdk/build-tools/`。

---

## 3. 工作流程与门禁设计

```text
[输入 APK] 
    │
    ▼
[1. 元数据解析与 SHA-256 计算]
    │
    ▼
[2. Fail-Closed 门禁校验] ──(不匹配)──► [立即阻断退出]
    │ (匹配)
    ▼
[3. 加固检测 (NetEase YiDun)] ──(检出)──► [发出加壳告警与运行时风险提示]
    │
    ▼
[4. apktool 解包至临时目录]
    │
    ▼
[5. Smali 规则检索与模式替换 (A1-A5)]
    │
    ▼
[6. apktool 回编译重构 APK]
    │
    ▼
[7. zipalign 4 字节对齐]
    │
    ▼
[8. apksigner v1+v2 签名并核验]
    │
    ▼
[输出 Patched APK + patch_report.json]
```

### 3.1 自适应版本门禁 (Fail-Closed)
为防止由于版本不符导致 Smali 字节错位或运行时崩溃，脚本执行严格的版本与校验和比对：
- 提取 `AndroidManifest.xml` 中的 `versionName`；
- 在补丁配置（默认模板 `patch_config.example.json`，复制改名后按需修改）的 `supported_versions` 白名单中检索该版本；
- 核验输入 APK 的 SHA-256 与配置白名单哈希；若存在任何差异，**立即中止退出**。

---

## 4. 命令行用法

### 4.1 基本命令
```bash
python3 patch_official_apk.py \
  --input /path/to/official.apk \
  --output /path/to/patched.apk \
  --config tools/android/patch_config.example.json \
  --days 24 \
  --report patch_report.json
```

### 4.2 参数说明
- `-i, --input`：原始 APK 输入路径。
- `-o, --output`：补丁输出 APK 路径。
- `-c, --config`：配置文件路径（默认 `tools/android/patch_config.example.json`）。
- `--days`：扩展天数目标（`21` 或 `24`，默认 `24`）。
- `-k, --keystore`：签名密钥库路径（缺省时自动生成标准 `debug.keystore`）。
- `--key-alias`：签名别名（默认 `androiddebugkey`）。
- `--key-pass`：密钥密码（默认 `android`）。
- `-r, --report`：生成的验证报告 JSON 输出路径（默认 `patch_report.json`）。
- `--skip-sign`：仅反编译并修改 Smali，不执行对齐与重签名。
- `--mock-test`：运行模拟自测，验证 Fail-Closed 门禁拒绝逻辑与 Smali 正则替换断言。

### 4.3 模拟自测验证
在无真实安装包或环境测试时，可执行：
```bash
python3 tools/android/patch_official_apk.py --mock-test
```
输出将演示：
1. 传入伪造哈希包时门禁正确拦截并 Fail-Closed；
2. 构造虚拟 Smali 树应用 A2/A3 规则并成功断言字节变换；
3. 输出结构化 `patch_report.json`。

---

## 5. 加壳防护与已知风险

### 5.1 官方包网易易盾加固 (NetEase YiDun)
经对官方发布版本（如 `02.26.01.00`）逆向审计发现：
- 官方 APK 采用网易易盾加壳保护（含有 `libnesec.so` 与 `assets/nedata.db`）；
- 核心业务代码（2700+ 类，包括 `Lcom/sisensing/` 系列）由底层 Native 库在内存中动态解密并注入 ClassLoader；
- `libnesec.so` 包含运行时签名与完整性校验。如果直接使用 apktool 反编译、修改并重签名，启动时易盾校验将判定签名被篡改，导致应用闪退。

### 5.2 应对与替代路线
针对加固版本，推荐以下处理方式：
1. **未加固/社区构建版本**：使用历史未加壳版本或社区构建，直接应用 `patch_official_apk.py` 进行静态 Smali 覆写；
2. **动态内存脱壳 (Dump)**：使用 Frida `dexdump`、FART 或 BlackDex 提取解密后的 Multidex，再进行静态打包；
3. **运行时 Hook (推荐)**：使用 Xposed / LSPosed 框架，在运行时直接 Hook `getSensorDays()`、`getSensorNum()` 与 `dealSonserStatusExpire()`，无需破坏 APK 签名，完全规避加固壳的静态完整性校验。

详细逆向分析与 Smali 候选站点模式参见 [docs/android-patching.md](../../docs/android-patching.md)。
