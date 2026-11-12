# Spec Delta

## Purpose

规定影像设备通过 DICOM C-STORE 向服务器传输图像时，服务器如何校验、持久保存、组织图像，以及如何向设备给出可信的成功或失败结果。覆盖客户原话中的“集中存储、按患者检查组织、并发接收处理”。

## ADDED Requirements

### Requirement: R5 持久化后才确认成功
当已授权设备通过 C-STORE 发送受支持的存储类实例时，系统 SHALL 原样保存收到的网络数据集字节（File Meta 由服务器生成），依次完成 fsync 落盘、读回校验、在同一数据库事务中提交索引 / 基线 / 审计，并写入审计锚点，全部完成后才返回 0x0000。落盘失败 SHALL 返回 0xA700，事务失败 SHALL 返回 0xC0FF，提交后锚点写入失败或锚点不可写 SHALL 返回 0xA701 并撤销本次入库。所有失败都 MUST NOT 留下索引记录或残留文件，并 SHALL 产生告警。

#### Scenario: TC-R5-P1 成功入库且数据集字节与发送端一致
- **WHEN** CT_SIM_1 以 Explicit VR Little Endian 发送一张合成 CT
- **THEN** 返回 0x0000，库中有且只有 1 条该实例记录；记录的 dataset_sha256 等于发送端文件中 File Meta 之后字节的 SHA-256；库内文件 File Meta 中的 ImplementationClassUID 是服务器的而不是发送端的

#### Scenario: TC-R5-P2 内镜与二次捕获入库
- **WHEN** ENDO_SIM_1 发送 2 张 RGB 内镜（VL Endoscopic）与 2 张二次捕获（SC）
- **THEN** 全部返回 0x0000，SOP 类与模态记录正确，dataset_sha256 与发送端字节一致

#### Scenario: TC-R5-N1 落盘失败返回 0xA700
- **WHEN** 通过故障注入让写文件失败，CT_SIM_1 发送一张 CT
- **THEN** 返回 0xA700，库中没有该实例，存储目录没有残留文件，产生 STORAGE_FAILURE 告警

#### Scenario: TC-R5-N2 事务失败返回 0xC0FF 并清理文件
- **WHEN** 通过故障注入让数据库提交失败，CT_SIM_1 发送一张 CT
- **THEN** 返回 0xC0FF，库中没有该实例，已写入的文件被删除

#### Scenario: TC-R5-N3 提交后锚点写入失败时撤销本次入库
- **WHEN** 只让 STORE_ACCEPTED 记录的锚点写入失败，发送一个新检查的 CT；恢复后重发；再对已有序列的另一张注入同样失败
- **THEN** 失败时返回 0xA701，该检查的患者 / 检查 / 序列 / 实例行与文件都不存在，审计有 STORE_ACCEPTED 及引用其 seq 的 STORE_ROLLED_BACK，并告警 AUDIT_ANCHOR_FAILURE；恢复后重发 0x0000、锚点补写、审计校验 ok；已有序列只撤销该实例，序列保留

#### Scenario: TC-R5-N4 锚点持续不可写时预先拒绝入库，恢复后可重发
- **WHEN** 锚点写入一律失败时发送 C-STORE、C-ECHO、C-FIND 并做审计校验，然后恢复写入并重发
- **THEN** 故障期间 C-STORE 返回 0xA701，索引 0 行、文件 0 个、无处理器异常，ECHO 与 FIND 照常成功；恢复后重发 0x0000，审计校验 ok 且未锚定记录为 0

### Requirement: R6 入库校验与层级一致性
系统 SHALL 拒绝以下 C-STORE 实例，并在 ErrorComment 中写明原因：缺少 Study / Series / SOP Instance UID（0xC001）；数据集中的 SOP Instance UID 与请求不一致（0xC002）；数据集中的 SOP Class UID 与请求不一致（0xA900）；Series 已归属其他 Study，或 Study 已归属其他 PatientID（0xC003）；数据集无法解析（0xC004）。被拒实例 MUST NOT 入库。

#### Scenario: TC-R6-P1 完整的数据集入库
- **WHEN** CT_SIM_1 发送属性完整的合成 CT
- **THEN** 返回 0x0000

#### Scenario: TC-R6-N1 缺少 SeriesInstanceUID 被拒
- **WHEN** 发送删除了 SeriesInstanceUID 的数据集
- **THEN** 返回 0xC001，ErrorComment 中含 “SeriesInstanceUID”，该实例不入库

#### Scenario: TC-R6-N2 SOP Instance UID 与请求不一致被拒
- **WHEN** C-STORE 请求的 Affected SOP Instance UID 与数据集中的 SOPInstanceUID 不同
- **THEN** 返回 0xC002，该实例不入库

#### Scenario: TC-R6-N3 Series 归属冲突被拒
- **WHEN** 发送一个实例，其 SeriesInstanceUID 已属于另一个 StudyInstanceUID
- **THEN** 返回 0xC003，原有层级不变

### Requirement: R7 重复实例与 UID 冲突
当收到的 SOP Instance UID 已经在库中时，系统 SHALL 先校验已存副本的完整性。已存副本完整，且新数据字节或内容（像素哈希与元数据快照）相同 → 返回 0x0000，不重复入库。已存副本完整但内容不同 → 返回 0xC010，新数据隔离，告警 UID_CONFLICT。已存副本缺失或被改 → 返回 0xC011，新数据隔离，告警 STORED_COPY_DAMAGED，MUST NOT 报成功。

#### Scenario: TC-R7-P1 相同实例重发幂等成功
- **WHEN** CT_SIM_1 把同一实例的同一份字节发送两次
- **THEN** 两次都返回 0x0000，库中仍只有 1 条记录，审计中有 STORE_DUPLICATE_IDENTICAL，告警数为 0

#### Scenario: TC-R7-P2 不同编码的同内容重发视为等价
- **WHEN** 同一实例先以 Explicit VR LE 入库，再以 Implicit VR LE 重发
- **THEN** 返回 0x0000，库中仍为 1 条记录，已存文件不变，审计中有 STORE_DUPLICATE_EQUIVALENT

#### Scenario: TC-R7-N1 同 UID 不同像素被拒并隔离
- **WHEN** 发送与已存实例同 UID、但像素不同的数据集
- **THEN** 返回 0xC010，已存文件与基线不变，隔离区新增 1 条记录，产生告警 UID_CONFLICT

#### Scenario: TC-R7-N2 已存副本损坏时重发不报成功
- **WHEN** 已存文件被篡改后，设备重发与原基线相同的数据
- **THEN** 返回 0xC011，产生告警 STORED_COPY_DAMAGED，库内文件不被自动覆盖

### Requirement: R8 按患者、检查、序列、实例组织
系统 SHALL 按 患者（PatientID + IssuerOfPatientID）→ 检查（StudyInstanceUID）→ 序列 → 实例 的层级组织已入库实例，并在 C-FIND 和管理界面中一致呈现。同一 PatientID 但人口学信息不一致时，系统 SHALL 只标记为“人口学不一致”，MUST NOT 产生篡改类告警。PatientID 为空的实例归入空 ID 患者，并在审计中注明。

#### Scenario: TC-R8-P1 同一患者的两个检查归在一起
- **WHEN** 患者 SYN0001 的两个检查（CT 与内镜）入库
- **THEN** PATIENT 级 C-FIND 返回 1 个患者，NumberOfPatientRelatedStudies 为 2

#### Scenario: TC-R8-N1 不同 PatientID 同姓名不合并
- **WHEN** PatientID 分别为 SYN0003 与 SYN0004、姓名都是 “TEST^SAME” 的实例入库
- **THEN** PATIENT 级查询 “TEST^SAME” 返回 2 个患者

#### Scenario: TC-R8-N2 人口学不一致只标记、不告警
- **WHEN** PatientID 为 SYN0001 但姓名不同的实例入库
- **THEN** 实例入库成功，该患者标记 demographics_conflict，告警数不增加

#### Scenario: TC-R8-N3 PatientID 为空时归入空 ID 患者
- **WHEN** PatientID 为空（Type 2）的 CT 入库
- **THEN** 返回 0x0000，患者表中有 patient_id 为空的记录，STORE_ACCEPTED 审计注明“PatientID 为空”

### Requirement: R12 并发接收与并发调阅
当多台设备同时传输、多个工作站同时查询和取回时，系统 SHALL 正确完成全部操作。实验阈值（未经客户确认）为：4 个存储 SCU 共 250 个实例与 2 个工作站各 3 轮取回同时进行，结果全部正确，总耗时不超过 300 秒；超过 max_associations 的关联 SHALL 被 A-ASSOCIATE-RJ（2,3,2）拒绝，服务 MUST NOT 崩溃。

#### Scenario: TC-CC1-P1 并发接收与并发取回
- **WHEN** CT_SIM_1/2 各发 100 张 CT、ENDO_SIM_1/2 各发 25 张内镜，与此同时 WS_SIM_1 做 3 轮 C-FIND + C-MOVE、WS_SIM_2 做 3 轮 C-FIND + C-GET，取回对象为预置的 100 张检查
- **THEN** 250 个 C-STORE 全部为 0x0000，库中恰好新增 250 个实例；每轮取回完成数为 100、失败数为 0，收到的数据集哈希与基线一致；总耗时不超过 300 秒并记录实际值

#### Scenario: TC-CC3-N1 超过关联上限被临时拒绝
- **WHEN** max_associations=8 时同时保持 9 个关联
- **THEN** 至少 1 个关联收到 A-ASSOCIATE-RJ（2,3,2），服务仍能响应 C-ECHO，释放后重试成功

#### Scenario: TC-CC4-N1 同 UID 并发竞争
- **WHEN** 两个 SCU 同时发送同一 SOP Instance UID：一组内容相同，另一组内容不同
- **THEN** 内容相同时两者都成功、库中 1 条；内容不同时恰好 1 个返回 0x0000、另 1 个返回 0xC010，库中 1 条

### Requirement: R13 传输中断不算事故
当设备在 C-STORE 数据传输中途中止关联（A-ABORT 或 TCP 断开）时，系统 SHALL 丢弃未完成的实例，只记录 ASSOCIATION_ABORTED 审计事件，MUST NOT 产生告警。设备重连并重发后，系统 SHALL 正常入库，且不产生重复。

#### Scenario: TC-R13-P1 中断后重发成功且无告警
- **WHEN** 设备发送一张 CT 到一半时中止，随后重连重发同一实例
- **THEN** 重发返回 0x0000，库中 1 条记录，告警数为 0

#### Scenario: TC-R13-N1 中断时未完成的实例不可见
- **WHEN** 设备发送到一半时中止
- **THEN** C-FIND 查不到该实例，存储目录中没有它的文件，审计中有 ASSOCIATION_ABORTED

### Requirement: R19 重启持久化与启动对账
系统重启后，已确认入库（0x0000）的实例 SHALL 仍可查询、取回，并能通过完整性核查。启动对账 SHALL 找出存储目录中没有索引的文件（孤儿文件），把它们移入隔离区，并产生 ORPHAN_FILE 告警；该告警需人工判断，不直接定性为篡改。

#### Scenario: TC-R19-P1 重启后数据仍在
- **WHEN** 实例入库后停止服务，再用同一 runtime 目录启动
- **THEN** C-FIND 能查到该实例，C-GET 能取回，核查结果为 0 异常

#### Scenario: TC-R19-N1 孤儿文件被发现并隔离
- **WHEN** 服务停止期间，在存储目录放入一个没有索引的 DICOM 文件，然后启动服务
- **THEN** 产生 ORPHAN_FILE 告警，该文件被移入隔离区，C-FIND 查不到它
