# Third-Party Notices / 第三方组件许可声明

本仓库主体代码与文档采用 MIT 许可证（见 [LICENSE](LICENSE)）。以下内嵌第三方组件**例外**于根 MIT 许可证，按其自身许可证分发：

## Gradle Wrapper

- 文件与归属：
  - `apps/android-cgmreader/gradle/wrapper/gradle-wrapper.jar` — © Gradle Inc.（上游 Gradle 项目构件）
  - `apps/android-cgmreader/gradlew` — Copyright © 2015-2021 the original authors（见文件头）
  - `apps/android-cgmreader/gradlew.bat` — Copyright 2015 the original author or authors（见文件头）
- 许可证：[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)（三个文件均在头/内嵌 META-INF 声明）
- 说明：Gradle 官方 Wrapper 引导件，用于固定构建工具链版本；上游项目地址：<https://github.com/gradle/gradle>

除此之外，本仓库不包含其他第三方二进制或源码副本（Android 构建依赖如 SDK、Gradle 插件由工具链按需在线拉取，不随仓库分发）。
