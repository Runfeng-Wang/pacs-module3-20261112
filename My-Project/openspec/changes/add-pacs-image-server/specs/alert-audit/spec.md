# Spec Delta

## Purpose

规定异常事件如何生成可追查的告警，以及服务器如何留存所有操作的审计记录并能检查记录本身是否被改动。对应客户原话“若发生变化应能发现并告警，所有操作应留存记录”。告警只在本地呈现，不对外推送。

## ADDED Requirements

### Requirement: R17 异常告警与正常操作零误报
当发生以下任一异常时，系统 SHALL 生成持久化告警，记录级别、类型、时间、对象标识、详情和建议处置，并在管理界面中显示；告警可被确认，确认动作写入审计。异常包括：未登记 / 地址不符 / 停用对端连接、未授权取回、未知 C-MOVE 目的地、完整性异常、调阅阻断、UID 冲突、已存副本损坏、存储失败、审计链断裂、审计锚点写入失败、核查过程异常、孤儿文件。同类型同对象的告警 SHALL 聚合计数。正常操作 MUST NOT 产生告警。

#### Scenario: TC-R17-P1 正常演示流程零告警
- **WHEN** 依次执行登记、C-ECHO、并发 C-STORE、C-FIND、C-MOVE、C-GET、相同实例重发、传输中断后重发、全量核查
- **THEN** 告警表为空

#### Scenario: TC-R17-N1 异常产生告警且同类聚合
- **WHEN** ROGUE 连续 3 次尝试关联，随后管理员确认这条告警
- **THEN** 只有 1 条 UNREGISTERED_PEER 告警，count 为 3，含对端 AE / IP 和建议处置；确认后状态为 acknowledged，审计中有 ALERT_ACK 记录

### Requirement: R18 哈希链审计与截断检测
系统 SHALL 为以下操作追加审计记录：关联的建立、拒绝、释放、中止，C-ECHO，每次 C-STORE，C-FIND（查询键与结果数），C-MOVE / C-GET（目的地、实例、子操作结果），白名单变更，告警生成与确认，核查运行，审计导出，服务启停。每条记录 SHALL 含前一条的哈希和 HMAC，并在提交后追加到数据库之外的锚点文件。审计校验 SHALL 报告链断点、序号缺口、尾部截断，以及与带外链头的不一致。锚点写入失败 MUST NOT 使任何操作抛出异常：系统 SHALL 告警 AUDIT_ANCHOR_FAILURE，并在恢复后补写全部未锚定记录。

#### Scenario: TC-R18-P1 操作与审计一一对应且链完整
- **WHEN** 执行 1 次 C-ECHO、1 次 C-STORE、1 次 C-FIND 后执行审计校验
- **THEN** 审计中依次出现 DICOM_ECHO、STORE_ACCEPTED、DICOM_FIND 记录；校验结果 ok，并给出链头 seq 与 hash

#### Scenario: TC-R18-N1 中间记录被修改
- **WHEN** 直接在数据库中修改第 k 条审计记录的 details 后执行审计校验
- **THEN** 校验失败，指出第一个断点为第 k 条，并产生 AUDIT_CHAIN_BROKEN 告警

#### Scenario: TC-R18-N2 尾部被截断
- **WHEN** 删除数据库中最后 3 条审计记录（锚点文件不动）后执行审计校验
- **THEN** 校验失败，报告锚点中的最大 seq 大于数据库中的最大 seq

#### Scenario: TC-R18-N4 锚点写入失败不使其他操作异常、恢复后补锚
- **WHEN** 锚点写入一律失败时新增白名单条目、产生一条告警、未登记 ROGUE 连接，然后恢复写入并执行审计校验
- **THEN** 三项操作都不抛异常（ROGUE 仍收到 RJ(1,1,3)），对应审计已写入数据库并告警 AUDIT_ANCHOR_FAILURE；恢复后审计校验 ok、未锚定记录为 0，并记录 AUDIT_ANCHOR_RECOVERED

#### Scenario: TC-R18-N3 与带外链头不一致
- **WHEN** 同时截断数据库与锚点文件的尾部后，用事先带外记录的链头执行审计校验
- **THEN** 校验失败，报告带外链头不在当前链中
