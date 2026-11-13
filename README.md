# 模块三 · 项目 B：PACS 图像服务器原型

这是一个围绕 PACS（医学影像归档与通信系统）需求构建的实验性原型。项目通过可运行的软件验证需求定义是否清楚、可实现、可验收；它用于课程验证，不是可部署到医院的临床系统。后续可根据需求变化重做或迭代。

## 项目内容

- [`模块三_需求定义文档.md`](模块三_需求定义文档.md)：多轮需求讨论形成的需求定义 v1.1，包含范围、角色、场景、验收条件、异常处理和外部互联约定。
- [`My-Project/`](My-Project/)：按 OpenSpec 工作流生成的完整 Python 项目，包含需求规格、设计、任务、源代码、测试和验证记录。

## 原型能做什么

- **接收影像**：实现 DICOM C-ECHO、C-STORE、C-FIND、C-MOVE、C-GET 服务端流程，并提供模拟设备和工作站。
- **管理连接准入**：按 AE Title、来源 IP、端口和角色维护白名单；对未登记或不匹配的连接拒绝并记录。
- **浏览影像**：按患者、检查、序列和实例查看合成影像与 DICOM 信息；支持对未压缩图像做管理用途预览。
- **核查完整性**：保存文件、数据集、像素及元数据基线；手动核查差异，调阅前按策略校验。
- **查看告警与审计**：在本地查看、确认告警；用带 HMAC 的哈希链和锚点检查审计记录，并导出 CSV。
- **提供操作入口**：PyQt5 管理控制台和 `pacs` 命令行共享同一套服务逻辑。

## 技术与项目结构

- Python 3.12+、pydicom、pynetdicom、NumPy、PyQt5、SQLite、uv。
- OpenSpec 项目位于 `My-Project/openspec/`：包含 `add-pacs-image-server` 变更、7 项能力规格及设计与任务记录。
- 实现、接口声明和测试报告位于 `My-Project/src/`、`My-Project/docs/` 与 `My-Project/tests/`。

## 快速体验（Windows）

安装 [uv](https://docs.astral.sh/uv/)，在 `My-Project` 目录的 PowerShell 中运行：

```powershell
uv sync
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-gui.ps1 -Runtime runtime\demo
```

演示会生成合成 DICOM、执行入库与查询流程，并注入一次影像篡改以展示核查和告警。启动后可在控制台的“影像库”“完整性核查”“告警”和“审计”页面查看结果。更完整的服务端、模拟设备和 DCMTK 操作方式见 [`My-Project/README.md`](My-Project/README.md)。

## 验证情况

本地验证记录包括 **91 项测试通过**、OpenSpec 严格校验通过，以及使用 DCMTK 3.7.1 对 DICOM 网络流程进行的本机互操作检查。测试范围和结果见 `My-Project/docs/testing/test-report.md`。这些结果验证的是本机合成数据和实验环境，不代表医院现场验收。

真实医院系统未连接；跨组 A 采集端与 C 阅片端联调尚未执行。默认参数仍是待确认的实验假设。操作员字段只记录姓名，不提供登录认证或角色权限控制；告警仅在本地显示。

## 使用边界

本项目只处理本机回环连接与合成 DICOM，不含真实患者数据，不用于诊断或临床决策。它用于检查需求与实现是否一致，并为后续需求评审和接口联调提供可验证的原型。
