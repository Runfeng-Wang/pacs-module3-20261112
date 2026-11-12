# Spec Delta

## Purpose

规定哪些 DICOM 对端可以与 PACS 建立关联、可以使用哪些服务，以及白名单如何在线维护。对应客户原话“仅登记在册的设备可以连接，白名单应支持维护更新”。身份要素为 AE Title + 源 IP，这属于准入控制，不是身份认证（实验基线假设，未经医院客户确认）。

## ADDED Requirements

### Requirement: R1 已登记对端的连通性验证
当已登记且启用的对端以正确的被叫 AE Title 发起 C-ECHO 时，系统 SHALL 返回状态 0x0000，并写入一条审计记录（对端 AE、IP、结果）。当被叫 AE Title 与服务器配置的 AE Title 不一致时，系统 SHALL 以 A-ASSOCIATE-RJ（result=1 permanent，source=1 service-user，reason=7 called-AE-not-recognized）拒绝，并写入审计。

#### Scenario: TC-R1-P1 已登记工作站 C-ECHO 成功
- **WHEN** 已登记的 WS_SIM_1 从 127.0.0.1 以被叫 AE “PACS_B” 发起 C-ECHO
- **THEN** 返回状态 0x0000，审计中出现事件类型 DICOM_ECHO、结果 success 的记录

#### Scenario: TC-R1-N1 被叫 AE 错误被拒
- **WHEN** 已登记的 WS_SIM_1 以被叫 AE “WRONG_AE” 请求关联
- **THEN** 对端收到 A-ASSOCIATE-RJ（1,1,7），审计中出现 ASSOCIATION_REJECTED 记录

### Requirement: R2 白名单准入
当对端请求建立关联时，系统 SHALL 以（Calling AE Title，源 IP）匹配白名单中的启用条目，只有匹配成功才进入表示上下文协商；未登记 AE、AE 已登记但 IP 不符、条目已停用这三种情况，系统 SHALL 以 A-ASSOCIATE-RJ（1,1,3 calling-AE-not-recognized）拒绝，写入审计，并产生告警（同一来源同类告警在时间窗口内聚合计数）。

#### Scenario: TC-R2-P1 登记设备被接受
- **WHEN** 白名单中登记为 127.0.0.1 的 CT_SIM_1 请求关联
- **THEN** 关联被接受

#### Scenario: TC-R2-N1 未登记 AE 被拒并告警
- **WHEN** 未登记的 ROGUE 请求关联
- **THEN** 对端收到 A-ASSOCIATE-RJ（1,1,3），产生告警 UNREGISTERED_PEER

#### Scenario: TC-R2-N2 AE 已登记但 IP 不符被拒
- **WHEN** 登记 IP 为 10.0.0.5 的 CT_SPOOF 从 127.0.0.1 请求关联
- **THEN** 对端收到 A-ASSOCIATE-RJ（1,1,3），产生告警 PEER_ADDRESS_MISMATCH

#### Scenario: TC-R2-N3 停用条目被拒
- **WHEN** 已停用的 CT_SIM_2 请求关联
- **THEN** 对端收到 A-ASSOCIATE-RJ（1,1,3），产生告警 PEER_DISABLED

### Requirement: R3 按角色授权服务
系统 SHALL 只接受与对端角色相符的表示上下文：STORE 对应存储类，QUERY 对应 FIND，RETRIEVE 对应 MOVE / GET。不符的表示上下文 SHALL 在协商时被拒绝，并写入审计；被拒的是 MOVE 或 GET 上下文时，SHALL 产生 UNAUTHORIZED_RETRIEVE 告警。所有已登记对端都可以使用 Verification。

#### Scenario: TC-R3-P1 具备 QUERY 角色的工作站可以 C-FIND
- **WHEN** 角色为 QUERY + RETRIEVE 的 WS_SIM_1 提议 Study Root FIND 上下文并发起 C-FIND
- **THEN** 上下文被接受，C-FIND 最终状态为 0x0000

#### Scenario: TC-R3-N1 只有 STORE 角色的设备不能取回
- **WHEN** 只有 STORE 角色的 CT_SIM_1 提议 Study Root MOVE 上下文
- **THEN** 该上下文被拒，对端无法发送 C-MOVE，产生告警 UNAUTHORIZED_RETRIEVE

#### Scenario: TC-R3-N2 只有 STORE 角色的设备不能查询
- **WHEN** 只有 STORE 角色的 CT_SIM_1 提议 Study Root FIND 上下文
- **THEN** 该上下文被拒，审计中记录 CONTEXT_DENIED

### Requirement: R4 白名单在线维护
当管理员通过 CLI 或图形界面新增、修改、启用、停用或删除白名单条目时，系统 SHALL 先校验输入（AE Title 1–16 个字符、不含反斜杠和控制字符、不能全为空格、不能重复；IP 地址合法；端口 1–65535；角色属于已定义集合），变更 SHALL 对之后新建的关联立即生效、无需重启，并写入包含变更前后值的审计。

#### Scenario: TC-R4-P1 新增条目立即生效
- **WHEN** 服务运行中新增 ENDO_SIM_3（127.0.0.1，STORE），随后 ENDO_SIM_3 发送 C-STORE
- **THEN** 不重启服务即返回 0x0000，审计中有 PEER_ADDED 记录及其后值

#### Scenario: TC-R4-N1 非法输入被拒绝保存
- **WHEN** 新增 AE Title 为 17 个字符、含反斜杠、与已有条目重复，或 IP 为 “999.1.1.1” 的条目
- **THEN** 每次都被拒绝并给出原因，白名单内容不变

#### Scenario: TC-R4-N2 停用后新关联被拒
- **WHEN** 停用 CT_SIM_1 后，CT_SIM_1 再次请求关联
- **THEN** 收到 A-ASSOCIATE-RJ（1,1,3），审计中有 PEER_UPDATED 记录，含 enabled 的前后值
