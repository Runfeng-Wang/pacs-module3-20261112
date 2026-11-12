# Spec Delta

## Purpose

规定服务器如何为每个入库实例建立完整性基线、如何核查库内影像是否仍与入库时一致、如何分类和报告变化，以及调阅时如何处理异常影像。对应客户原话“库内图像必须与上传时保持一致，若发生变化应能发现并告警”。“上传时”指服务器接收完成的时刻，不包括设备端的源文件。

## ADDED Requirements

### Requirement: R14 入库时建立受保护的基线
每个实例入库成功时，系统 SHALL 记录以下基线：文件 SHA-256、网络数据集字节 SHA-256、像素数据原始字节 SHA-256、完整的非像素元数据快照、未压缩像素的分块哈希，以及来源 AE / IP、接收时间、传输语法。基线 SHALL 以 HMAC-SHA256 保护，密钥保存在数据目录之外。基线 HMAC 不符、基线缺失、JSON 无法解析或结构无效时，系统 SHALL 报告 BASELINE_TAMPERED，跳过无法比对的部分，并 MUST NOT 中断其他实例的核查。

#### Scenario: TC-R14-P1 基线可以复算
- **WHEN** 一张 CT 入库后立即对其执行核查
- **THEN** 复算得到的文件、数据集、像素哈希与基线完全一致，HMAC 校验通过，结果为 OK

#### Scenario: TC-R14-N1 基线记录被改动后能检出
- **WHEN** 直接在数据库中修改该实例基线的 file_sha256
- **THEN** 核查结果包含 BASELINE_TAMPERED，并产生告警

#### Scenario: TC-R14-N2 基线损坏或缺失时报告且核查不中断
- **WHEN** 5 张 CT 中：一张的 meta_json 改成“{”；一张的 tiles_json 改成“[1]”且像素被改；一张的基线行被删除；一张的 meta_json 改成“[]”且 StudyDate 被改；然后全量核查
- **THEN** 核查完成（checked=5、ok=1）；四张都报告 BASELINE_TAMPERED，像素与元数据变化仍被报告并注明无法给出位置或前值的原因；BASELINE_TAMPERED 告警 4 条

### Requirement: R15 完整性核查与分类报告
当管理员或质控人员对全部或选定对象运行完整性核查时，系统 SHALL 逐实例比对基线，并对每个异常输出：患者 ID、检查 / 序列 / 实例 UID、类别（FILE_MISSING、UNREADABLE、PIXEL_CHANGED、METADATA_CHANGED、STRUCTURE_CHANGED、INDEX_MISMATCH、BASELINE_TAMPERED；单实例核查出现意外异常时为 VERIFY_ERROR 且继续核查其他实例）、字段或位置、前值与后值、检测时间、最后一次合法访问记录、建议处置。核查本身 MUST NOT 修改任何库内文件。

#### Scenario: TC-R15-P1 正常业务操作后没有误报
- **WHEN** 库内实例经过多次 C-MOVE / C-GET、相同实例重发、服务重启和白名单变更之后，执行全量核查
- **THEN** 异常数为 0、新告警数为 0，全部库内文件的 SHA-256 与核查前相同

#### Scenario: TC-R15-N1 像素被改动
- **WHEN** 把某个 CT 文件像素区中的 1 个字节翻转后执行核查
- **THEN** 该实例报告 PIXEL_CHANGED，并给出变化所在的帧号与像素块行列范围

#### Scenario: TC-R15-N2 检查日期被改动
- **WHEN** 把某个文件的 StudyDate 由 20261105 改为 20261106 后执行核查
- **THEN** 该实例报告 METADATA_CHANGED，字段为 (0008,0020) StudyDate，前值 20261105，后值 20261106，标记为关键字段

#### Scenario: TC-R15-N3 文件被删除
- **WHEN** 删除某个库内文件后执行核查
- **THEN** 该实例报告 FILE_MISSING

#### Scenario: TC-R15-N4 只改数据库索引、文件看似正常
- **WHEN** 只修改数据库中该患者的 PatientName（文件不动）后执行核查
- **THEN** 报告 INDEX_MISMATCH，给出索引值与基线值

#### Scenario: TC-R15-N5 单实例核查异常不阻断其他实例
- **WHEN** 第 3 张 CT 的核查过程抛出意外异常，然后执行全量核查
- **THEN** 核查完成，其余 4 张为正常；该实例报告 VERIFY_ERROR（含异常类型），并告警 VERIFY_ERROR

### Requirement: R16 调阅前校验与默认阻断
系统在 C-MOVE / C-GET 发送每个实例之前 SHALL 先校验其完整性。默认策略为“阻断”（实验基线假设，未经影像科确认）：未通过校验的实例 MUST NOT 被发送，计为失败子操作并列入 Failed SOP Instance UID List，同时产生 RETRIEVE_BLOCKED 告警。为对端在未压缩传输语法之间做无损转换属于正常业务，MUST NOT 告警。

#### Scenario: TC-R16-P1 未改动的实例正常发送
- **WHEN** 对一个未改动的检查执行 C-MOVE
- **THEN** 全部实例发送成功，没有 RETRIEVE_BLOCKED 告警

#### Scenario: TC-R16-P2 无损传输语法转换不告警
- **WHEN** 库内以 Explicit VR LE 保存的实例，被只接受 Implicit VR LE 的工作站 C-GET
- **THEN** 子操作成功；收到的像素数据字节哈希与基线 pixel_sha256 一致；库内文件不变；告警数为 0

#### Scenario: TC-R16-N2 基线损坏或缺失的实例在调阅时被阻断
- **WHEN** 检查中一张的 meta_json 被改成“{”、另一张的基线行被删除，然后对该检查执行 C-MOVE
- **THEN** 最终 0xB000，完成 3、失败 2，Failed SOP Instance UID List 恰为这两张，取回过程不中断，RETRIEVE_BLOCKED 告警 2 条

#### Scenario: TC-R16-N1 被篡改的实例被阻断
- **WHEN** 一个检查中有 1 张实例的像素被改动，对该检查执行 C-MOVE
- **THEN** 该实例没有被发送；最终状态为 0xB000，失败数为 1，Failed SOP Instance UID List 中含该实例；产生 RETRIEVE_BLOCKED 告警
