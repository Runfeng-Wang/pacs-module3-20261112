# Spec Delta

## Purpose

规定影像科工作站如何通过 DICOM 标准协议（C-FIND / C-MOVE / C-GET，Patient Root 与 Study Root）查询和调阅服务器上的图像，以及工作站据以判断调阅成功或失败的计数与状态。对应客户原话“支持随时的查询与调阅；影像科工作站应通过 DICOM 标准协议向服务器调阅图像”。

## ADDED Requirements

### Requirement: R9 C-FIND 分层查询
系统 SHALL 支持 Patient Root 与 Study Root 信息模型在 PATIENT / STUDY / SERIES / IMAGE 层级的分层查询。匹配方式包括单值、通配符（* ?）、日期范围、UID 列表和通用匹配；结果以 0xFF00 逐条返回，以 0x0000 结束。缺少上层唯一键时 SHALL 返回 0xA900；收到 C-CANCEL 时 SHALL 以 0xFE00 结束。

#### Scenario: TC-R9-P1 按 PatientID 查询检查
- **WHEN** WS_SIM_1 以 Study Root、STUDY 级、PatientID=SYN0001 发起 C-FIND
- **THEN** 恰好返回 SYN0001 的全部检查，每条都含 StudyInstanceUID、StudyDate、ModalitiesInStudy、NumberOfStudyRelatedInstances，最终状态为 0x0000

#### Scenario: TC-R9-P2 通配符、日期范围与中文姓名匹配
- **WHEN** 分别以 PatientName=“TEST*”、StudyDate=“20261101-20261130”、PatientName=“测试^患者二”（SpecificCharacterSet 为 ISO_IR 192）查询
- **THEN** 每次都恰好返回预期的检查集合

#### Scenario: TC-R9-N1 无匹配时返回空结果且成功
- **WHEN** 以不存在的 PatientID=NOPE 查询
- **THEN** 没有 0xFF00 响应，最终状态为 0x0000

#### Scenario: TC-R9-N2 缺少上层唯一键被拒
- **WHEN** 以 Study Root、SERIES 级发起查询，但不带 StudyInstanceUID
- **THEN** 最终状态为 0xA900

#### Scenario: TC-R9-N3 查询中途取消
- **WHEN** 通配符查询返回第一条 0xFF00 后，对端发送 C-CANCEL
- **THEN** 最终状态为 0xFE00

### Requirement: R10 C-MOVE 推送到已登记目的地
当具备 RETRIEVE 角色的对端发起 C-MOVE 时，系统 SHALL 只从白名单中具备 MOVE_DEST 角色的启用条目读取目的地地址和端口，向该目的地发起 C-STORE 子操作，并报告剩余、完成、失败、警告计数。目的地未登记、已停用或没有 MOVE_DEST 角色时，系统 SHALL 返回 0xA801，MUST NOT 发起任何外连，并产生 UNKNOWN_MOVE_DESTINATION 告警。

#### Scenario: TC-R10-P1 推送整个检查到已登记工作站
- **WHEN** WS_SIM_1 请求把 100 张的检查 C-MOVE 到 WS_SIM_1
- **THEN** 最终状态为 0x0000，完成数为 100、失败数为 0；WS_SIM_1 收到的每个数据集哈希都与基线 dataset_sha256 一致

#### Scenario: TC-R10-N1 未登记目的地被拒且不外连
- **WHEN** WS_SIM_1 请求 C-MOVE 到未登记的 UNKNOWN_WS
- **THEN** 最终状态为 0xA801，服务器没有发起出站关联，产生告警 UNKNOWN_MOVE_DESTINATION

#### Scenario: TC-R10-N2 目的地被停用后被拒
- **WHEN** 停用 WS_SIM_2 后，WS_SIM_1 请求 C-MOVE 到 WS_SIM_2
- **THEN** 最终状态为 0xA801，产生告警 UNKNOWN_MOVE_DESTINATION

### Requirement: R11 C-GET 在同一关联内取回
当具备 RETRIEVE 角色的对端发起 C-GET 时，系统 SHALL 在同一关联中以 C-STORE 子操作返回全部匹配实例，并报告计数。对端没有为某个存储类协商 SCP 角色时，该实例的子操作 SHALL 计为失败，最终状态为 0xB000（部分失败）或 0xA702（全部失败），并附 Failed SOP Instance UID List。收到 C-CANCEL 时 SHALL 以 0xFE00 结束。

#### Scenario: TC-R11-P1 取回整个序列
- **WHEN** WS_SIM_2 以 SERIES 级对一个 25 张的内镜序列发起 C-GET
- **THEN** 最终状态为 0x0000，完成数为 25，收到的数据集哈希与基线一致

#### Scenario: TC-R11-N1 未协商存储 SCP 角色时子操作失败
- **WHEN** WS_SIM_2 发起 C-GET，但没有为 CT Image Storage 协商 SCP 角色
- **THEN** 最终状态为 0xA702，失败数等于实例数，Failed SOP Instance UID List 中含这些实例

#### Scenario: TC-R11-N2 取回中途取消
- **WHEN** C-GET 收到第一个子操作后，对端发送 C-CANCEL
- **THEN** 最终状态为 0xFE00，完成数小于实例总数
