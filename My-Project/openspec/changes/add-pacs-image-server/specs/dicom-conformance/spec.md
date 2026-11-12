# Spec Delta

## Purpose

规定服务器对外公布的 DICOM 互联约定：支持和不支持哪些服务、SOP 类、传输语法，各状态码的含义，以及收到不支持的服务请求时的协议行为。目的是让 A 组（采集）和 C 组（阅片）的对端只看文档就能对接，也明确 C-STORE 成功不等于 Storage Commitment。

## ADDED Requirements

### Requirement: R21 按成文接口文档可对接
系统 SHALL 提供成文的 DICOM 一致性声明，列出 AE 与端口配置、支持的 SOP 类与传输语法、C-FIND 键与匹配规则、各服务的状态码含义及 SCU 处置建议、准入登记流程、超时与限制、已知限制。对端按文档使用独立的标准 SCU 工具，SHALL 能完成 echo / store / find / move / get。对于文档声明不支持的服务（含 Storage Commitment Push Model），系统 SHALL 在协商时拒绝其表示上下文。

#### Scenario: TC-R21-P1 按文档用标准工具完成五种服务
- **WHEN** 只依据一致性声明中的参数，用 pynetdicom 以外的独立 SCU 实现（可用时用 DCMTK echoscu / storescu / findscu / movescu / getscu；不可用时用 pynetdicom 自带应用并降低证据级别）依次执行 echo、store、find、move、get
- **THEN** 五项都按文档描述的状态结束，数据可在服务器中查到

#### Scenario: TC-R21-N1 Storage Commitment 上下文被拒
- **WHEN** 已登记对端提议 Storage Commitment Push Model（1.2.840.10008.1.20.1）表示上下文
- **THEN** 该上下文被拒（abstract syntax not supported），对端不能发出 N-ACTION

#### Scenario: TC-R21-N2 不支持的传输语法被拒
- **WHEN** 已登记设备只以文档未列出的传输语法（例如 Deflated Explicit VR Little Endian）提议 CT Image Storage
- **THEN** 该上下文被拒（transfer syntaxes not supported），对端不能发出 C-STORE
