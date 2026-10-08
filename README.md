# cgm-bridge

开源持续葡萄糖监测（CGM）硬件数据互操作工具链与原生独立读取器。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![iOS Status](https://img.shields.io/badge/iOS-v0_Logger-blue.svg)](#)
[![Android Status](https://img.shields.io/badge/Android-v0_Logger-blue.svg)](#)

---

## 🎯 项目定位

`cgm-bridge` 旨在解决闭源持续葡萄糖监测硬件与开放数据生态（如 Apple HealthKit、Android Health Connect、Nightscout）之间的数据孤岛问题，赋予用户对其自身生理数据的完全掌控权。

本项目提供两条互补的技术实施路线：
1. **路线 A：官方 App 补丁工具链（Patch Toolchain）**
   - 针对官方客户端（`com.sisensing.eco`）的字节补丁与重打包流程。
   - 实现生命周期限制解除、脱机本地数据导出增强等能力。
   - 配套运行时组件：[状态掩码 dylib](tools/dylib/README.md)（抑制探头失效门禁自我复活，保持数据管线连通）。
   - ⚠️ **24 天扩展 = 数据管线连续性，非精度背书**：传感器寿命末期精度随寿命递减是物理规律，详见 [数据质量实证](docs/data-quality.md)。
2. **路线 B：独立读取器（Standalone CGMReader）**
   - 不依赖任何官方闭源库的原生独立应用。
   - 原生 BLE 直连与原始通信帧捕获。
   - **状态**：v0 帧记录器已交付 iOS/Android；协议解析/算法/HealthKit 写入属 v1-v3 路线图未交付。

---

## 📁 目录结构

```
cgm-bridge/
├── LICENSE                 # MIT 开源许可证
├── README.md                # 项目主说明文档
├── DISCLAIMER.md            # 法律免责与医疗安全声明
├── SANITIZATION.md          # 严格脱敏契约与规范
├── .gitignore               # 二进制、依赖与私有文件忽略规则
├── docs/                    # 详细架构与协议文档
│   ├── architecture.md      # 双路线技术架构与 Mermaid 数据流
│   └── data-quality.md      # 传感器寿命末期数据质量实证与精度分级（重要）
├── tools/                   # 路线 A：补丁与自动化工具链
│   ├── ios/                 # iOS IPA 补丁、校验与重签名脚本
│   ├── android/             # Android APK 反编译与补丁脚本
│   └── dylib/               # 运行时状态掩码 dylib（探头失效 gate 抑制）
└── apps/                    # 路线 B：独立客户端工程
    ├── ios-cgmreader/       # iOS Swift/SwiftUI 原生独立帧记录器 (v0)
    └── android-cgmreader/   # Android Kotlin 原生独立帧记录器 (v0)
```

---

## ⚡ 快速开始

### 1. 路线 A：官方 App 补丁生成（iOS）
> 确保本地拥有个人合法提取之官方客户端脱壳 IPA 包（如版本 3.9.4）。

```bash
# 进入 iOS 补丁工具目录
cd tools/ios

# 执行版本校验、打补丁与 ldid 自动重签名
python3 patch_official_ipa.py <decrypted.ipa> <patched.ipa>
```

### 2. 路线 B：原生独立读取器（iOS CGMReader）
> 需配备具备 Xcode / Swift 编译环境的 macOS。

编译与部署步骤请参阅 [apps/ios-cgmreader/README.md](apps/ios-cgmreader/README.md)（采用 `xcrun swiftc` 无工程单文件编译流程，交付 v0 帧记录器）。

更多技术细节请参阅 [架构与数据流文档](docs/architecture.md)。

---

## ⚖️ 免责声明

在阅读、克隆或使用本项目代码前，请务必完整阅读 [DISCLAIMER.md](DISCLAIMER.md)。
- **非医疗器械**：本项目所有成果仅用于个人数据互操作性与协议研究，严禁用于临床医疗或治疗决策。
- **无官方关联**：与硅基仿生或任何相关实体无隶属关系。
- **无专有分发**：本仓库严禁且绝不包含官方应用专有安装包（IPA/APK）、受保护动态库或二进制副本。

---

## 🔒 脱敏与安全承诺

本项目执行严苛的 [脱敏规范契约 (SANITIZATION.md)](SANITIZATION.md)。仓库所有代码、文档及提交历史中绝不包含任何生产设备序列号、真实 BLE 名称、真实蓝牙 MAC、个人 IP、私钥凭据或容器唯一标识符。
