# PACS B（实验原型）DICOM 一致性声明 / 互联接口文档

> 目的：让 A 组（采集）与 C 组（阅片）的对端只看本文档就能对接。
> 版本：pacs-b-prototype 0.1.0（2026-11-12）。实现：Python 3.12 + pynetdicom 3.0.4 + pydicom 3.0.2。
> Implementation Class UID：`2.25.168856098296771086472905059958299090808`；Implementation Version Name：`PACS_B_0_1_0`。
> 结构参照 DICOM PS3.2，但这**不是**正式的产品一致性声明。所有参数都是“实验基线假设，未经医院客户确认”。
> **跨组真实联调尚未执行**（见 `cross-group-integration-plan.md`）。

证据级别：E0 = 仅声明；E1 = 单元测试；E2 = pynetdicom 真实 TCP 回环；E3 = DCMTK 3.7.1 独立实现回环；H = 真人；X = 跨组跨主机（**未执行**）。

## 1. 网络与关联参数

| 项 | 默认值 | 说明 |
|---|---|---|
| AE Title | `PACS_B` | 可在 `runtime/pacs.json` 修改；被叫 AE 必须与之一致，否则 RJ(1,1,7) |
| 监听地址 / 端口 | `127.0.0.1:11112` | 跨组联调时改为局域网地址（`pacs init --host <IP>`），并由用户自行放行 Windows 防火墙。本原型不修改系统设置 |
| 最大关联数 | 20 | 超过时 A-ASSOCIATE-RJ（result=2 transient，source=3，reason=2 local-limit-exceeded），稍后重试即可（E2：TC-CC3-N1） |
| 最大 PDU（接收） | 16384 字节 | 可配置 |
| ACSE / DIMSE / 网络超时 | 30 s / 60 s / 60 s | 可配置 |
| 安全 | 无 TLS、无 User Identity | 准入基于 AE Title + IP（不是认证） |
| 异步操作窗口 | 不支持（1,1） | |

## 2. 准入登记（对接前必须完成）

对端向 B 的管理员提供以下信息，由管理员用 `pacs peer add` 或控制台“白名单”页登记。变更对新关联**立即生效**，无需重启。

| 字段 | 要求 |
|---|---|
| AE Title | 1–16 个可打印 ASCII 字符，不含反斜杠，不能全为空格；区分大小写；去掉首尾空格后精确匹配 |
| IP | 对端发起连接时的源 IP（精确匹配，IPv4 / IPv6） |
| 角色 | `STORE`（向 B 发 C-STORE）、`QUERY`（C-FIND）、`RETRIEVE`（C-MOVE / C-GET）、`MOVE_DEST`（作为 C-MOVE 目的地） |
| 端口 | 角色含 `MOVE_DEST` 时必填：对端存储 SCP 的监听端口 |

关联拒绝（E2：TC-R1-N1、TC-R2-*；E3：DCMTK 未登记 AE）：

| 情形 | A-ASSOCIATE-RJ | 告警 |
|---|---|---|
| Calling AE 未登记 | result=1, source=1, reason=3 | UNREGISTERED_PEER |
| AE 已登记但源 IP 不符 | 1, 1, 3 | PEER_ADDRESS_MISMATCH |
| 条目已停用 | 1, 1, 3 | PEER_DISABLED |
| 被叫 AE 不是 `PACS_B` | 1, 1, 7 | WRONG_CALLED_AE |
| 关联数达到上限 | 2, 3, 2 | 无（只审计） |

表示上下文按角色授权（E2：TC-R3-*）：不具备相应角色的上下文在协商时被拒（result=3 abstract-syntax-not-supported）。非 RETRIEVE 角色的对端提议 MOVE / GET 上下文时，会产生 UNAUTHORIZED_RETRIEVE 告警。

## 3. B 作为 SCP 支持的服务

| 服务 | SOP Class UID | 证据 |
|---|---|---|
| Verification | 1.2.840.10008.1.1 | E2 + E3 |
| Storage（120 个存储类，见附录 A `storage-sop-classes.md`） | 例：CT 1.2.840.10008.5.1.4.1.1.2；VL Endoscopic 1.2.840.10008.5.1.4.1.1.77.1.1；SC 1.2.840.10008.5.1.4.1.1.7 | CT：E2 + E3；内镜、SC：E2；其余：E0 |
| Patient Root Q/R FIND | 1.2.840.10008.5.1.4.1.2.1.1 | E2（TC-R8-*） |
| Study Root Q/R FIND | 1.2.840.10008.5.1.4.1.2.2.1 | E2 + E3 |
| Patient Root Q/R MOVE | 1.2.840.10008.5.1.4.1.2.1.2 | E0（与 Study Root 共用实现） |
| Study Root Q/R MOVE | 1.2.840.10008.5.1.4.1.2.2.2 | E2 + E3 |
| Patient Root Q/R GET | 1.2.840.10008.5.1.4.1.2.1.3 | E0（与 Study Root 共用实现） |
| Study Root Q/R GET | 1.2.840.10008.5.1.4.1.2.2.3 | E2 + E3 |

**不支持**（协商时拒绝，result=3）：**Storage Commitment Push Model 1.2.840.10008.1.20.1**（E2：TC-R21-N1），以及 MWL、MPPS、N-服务、DICOMweb / WADO、HL7。

### 3.1 传输语法（存储类，按 B 的优先顺序选择）

| 顺序 | 传输语法 | UID |
|---|---|---|
| 1 | Explicit VR Little Endian | 1.2.840.10008.1.2.1 |
| 2 | Implicit VR Little Endian | 1.2.840.10008.1.2 |
| 3 | JPEG Lossless SV1 | 1.2.840.10008.1.2.4.70 |
| 4 | JPEG-LS Lossless | 1.2.840.10008.1.2.4.80 |
| 5 | JPEG 2000 Lossless | 1.2.840.10008.1.2.4.90 |
| 6 | RLE Lossless | 1.2.840.10008.1.2.5 |
| 7 | JPEG Baseline (8-bit) | 1.2.840.10008.1.2.4.50 |
| 8 | JPEG 2000 | 1.2.840.10008.1.2.4.91 |
| 9 | Explicit VR Big Endian（已退役，兼容用） | 1.2.840.10008.1.2.2 |

- 对一个提议了多个传输语法的上下文，B 按上表顺序选第一个匹配项（pynetdicom 的接受方规则）。
- 压缩数据**原样保存、不解码**；未列出的语法（例如 Deflated 1.2.840.10008.1.2.1.99）会被拒（result=4，E2：TC-R21-N2）。
- Q/R 上下文只接受 Explicit / Implicit VR LE。
- 证据：未压缩语法为 E2 / E3；压缩语法为 E0（本期没有压缩样本）。

## 4. C-STORE（A 组 → B）

**成功语义**：B 返回 `0x0000`，表示该实例已原样写盘并 fsync、读回校验、在同一事务中提交了索引、基线与审计，**并且该审计记录已写入审计锚点**（2026-11-12 独立复核后修复，见 design D-L）。
**这不是 Storage Commitment**：B 不发送 N-EVENT-REPORT，也不承诺长期保管。需要存储承诺的对端，本期无法获得。

B 保存的是 C-STORE 中**收到的网络数据集字节**；文件的 File Meta 由 B 生成（ImplementationClassUID 为 B 的，SourceApplicationEntityTitle 为发送方 AE）。B 不保存设备源文件的 File Meta 与前导。

| 状态 | 含义 | SCU 处置建议 |
|---|---|---|
| 0x0000 | 已持久化并建立基线；或同一实例重复发送（字节相同或内容等价），B 保留已存副本 | 视为已送达 |
| 0xA700 | Out of Resources：落盘失败（磁盘满或权限问题） | 可稍后重试；同时通知 B 管理员 |
| 0xA701 | Out of Resources：B 的审计锚点不可写，本次**未入库**（提交后锚点失败的已被补偿撤销，或在降级期间被预先拒绝） | 可稍后重试；B 恢复后会自动补写锚点并恢复接收。极少数情况下撤销也失败，此时 ErrorComment 为 “stored, retry later”，表示实例已入库但锚点待补，重发会得到 0x0000 |
| 0xA900 | 数据集 SOP Class UID 与请求不符 | 不要重试，修正数据 |
| 0xC001 | 缺少必需属性（SOPClassUID / SOPInstanceUID / StudyInstanceUID / SeriesInstanceUID），ErrorComment 列出缺失项 | 不要重试，修正数据 |
| 0xC002 | 数据集 SOPInstanceUID 与请求的 Affected SOP Instance UID 不一致 | 不要重试，修正数据 |
| 0xC003 | 层级冲突：Series 已属于另一个 Study，或 Study 已属于另一个 PatientID | 不要重试，联系 B 管理员核对 UID |
| 0xC004 | 数据集无法解析 | 不要重试 |
| 0xC010 | 同一 SOP Instance UID 已存在但内容不同（像素或元数据）；新数据已隔离 | 不要用同一 UID 重发改动后的数据（DICOM 规定内容变化必须用新 UID） |
| 0xC011 | 重发时发现 B 的已存副本完整性异常，B 无法确认；新数据已隔离 | 不要反复重试，联系 B 管理员 |
| 0xC0FF | 内部事务失败（数据库） | 可稍后重试；同时通知 B 管理员 |
| 0x0124 | Refused: Not Authorized（纵深防御：对端没有 STORE 角色） | 申请 STORE 角色 |

说明：PS3.4 没有为“同 UID 内容冲突”等情形定义专用码，因此 B 使用 C-STORE 失败类的 Cxxx（Cannot Understand）区段，并附 ErrorComment（ASCII，不超过 64 字符）。SCU 按“失败”类别处理即可。

审计锚点降级期间（B 告警 AUDIT_ANCHOR_FAILURE）：C-STORE 一律返回 0xA701；C-ECHO / C-FIND / C-MOVE / C-GET 照常可用，记录先写入 B 的数据库，恢复后补写锚点。

传输中断（A-ABORT 或 TCP 断开）：未完成的实例不入库，B 只审计、不告警；重连后重发即可，不会产生重复（E2：TC-R13-P1）。

## 5. C-FIND（C 组 → B）

- 信息模型：Patient Root（PATIENT / STUDY / SERIES / IMAGE）、Study Root（STUDY / SERIES / IMAGE）。
- 只支持分层查询。必须给出上层唯一键，否则返回 `0xA900`：
  - Patient Root：STUDY 级需要 PatientID；SERIES 级需要 PatientID + StudyInstanceUID；IMAGE 级再加 SeriesInstanceUID。
  - Study Root：SERIES 级需要 StudyInstanceUID；IMAGE 级需要 StudyInstanceUID + SeriesInstanceUID。
- 支持的键（既可作匹配键，也可作返回键）：
  - PATIENT：PatientName、PatientID、IssuerOfPatientID、PatientBirthDate、PatientSex、NumberOfPatientRelatedStudies / Series / Instances
  - STUDY：StudyInstanceUID、StudyDate、StudyTime、AccessionNumber、StudyID、StudyDescription、ReferringPhysicianName、ModalitiesInStudy、NumberOfStudyRelatedSeries、NumberOfStudyRelatedInstances
  - SERIES：SeriesInstanceUID、Modality、SeriesNumber、SeriesDescription、NumberOfSeriesRelatedInstances
  - IMAGE：SOPInstanceUID、SOPClassUID、InstanceNumber
  - 不支持的键原样返回为空值；序列（SQ）键返回空序列。
- 匹配规则：
  - 空值 = 通用匹配
  - 单值精确匹配（PN 不区分大小写，其他区分）
  - 通配符 `*`、`?`
  - DA / TM 范围 `A-B`、`-B`、`A-`
  - UI 用反斜杠分隔的 UID 列表
  - 多值查询（如 `ModalitiesInStudy=CT\ES`）任一匹配即可
- 响应：每个匹配一个 `0xFF00`，最后 `0x0000`；无匹配时直接 `0x0000`（TC-R9-N1）。响应中 SpecificCharacterSet = `ISO_IR 192`（UTF-8），RetrieveAETitle = `PACS_B`。查询请求可以使用 ISO_IR 192 编码中文姓名（TC-R9-P2）。
- C-CANCEL：以 `0xFE00` 结束（TC-R9-N3）。
- **调阅的“期望张数”**：请用 STUDY / SERIES 级返回的 `NumberOfStudyRelatedInstances` / `NumberOfSeriesRelatedInstances`。

## 6. C-MOVE（C 组 → B → C 组的存储 SCP）

- 目的地地址**只从 B 的白名单取**：条目必须已启用、具有 `MOVE_DEST` 角色并登记了端口。请求中的其他地址信息一概不用。
- 取回级别：PATIENT（Patient Root）、STUDY、SERIES、IMAGE，使用该级别的唯一键（可以是 UID 列表）。
- 子操作：B 作为存储 SCU 连接目的地，为每个涉及的 SOP 类提议“原存储语法 + Explicit VR LE + Implicit VR LE”。未压缩语法之间的转换是无损的，不告警（TC-R16-P2）。
- 状态：

| 状态 | 含义 |
|---|---|
| 0xFF00 | 进行中（带剩余 / 完成 / 失败 / 警告计数） |
| 0x0000 | 全部子操作成功 |
| 0xB000 | 部分子操作失败；Identifier 中的 Failed SOP Instance UID List (0008,0058) 列出失败实例 |
| 0xA702 | 全部子操作失败 |
| 0xA801 | **两种原因**：① 目的地未登记 / 已停用 / 不具备 MOVE_DEST（B 不发起任何外连，并告警 UNKNOWN_MOVE_DESTINATION）；② 目的地已登记但连不上（pynetdicom 行为）。B 的审计会区分这两种原因 |
| 0xA900 | 取回标识符缺少该级别唯一键（B 会先与已登记目的地建立关联，再返回此状态） |
| 0xFE00 | 已取消 |

- **调阅成功判据（C 组请照此实现）**：最终状态 `0x0000`，并且“完成数 = C-FIND 给出的期望张数”、失败数为 0。
- **完整性阻断（默认策略 block）**：发送前逐实例校验。未通过的实例**不发送**，计为失败子操作并列入 Failed SOP Instance UID List，同时告警 RETRIEVE_BLOCKED（TC-R16-N1）。配置为 `allow` 时会照常发送并告警（未测试，E0）。B 本期不在 DICOM 报文中携带“已篡改”的私有标记（见跨组待确认 Q-C3）。

## 7. C-GET（C 组 ← B，同一关联）

- 对端必须为希望接收的存储类提议表示上下文，并通过 SCP/SCU Role Selection 协商 **SCP 角色**；否则这些子操作失败，最终 `0xA702` / `0xB000`（TC-R11-N1）。
- 状态与计数同 C-MOVE（没有 0xA801）。支持 C-CANCEL（`0xFE00`，TC-R11-N2）。完整性阻断规则同上。

## 8. 对接示例命令

A 组（采集端）发送图像，以 DCMTK 为例（E3 已在本机回环中验证）：

```
echoscu  -aet <你的AE> -aec PACS_B <B的IP> 11112
storescu -aet <你的AE> -aec PACS_B <B的IP> 11112 image1.dcm image2.dcm
```

C 组（阅片端）：

```
findscu -S -aet <你的AE> -aec PACS_B -k QueryRetrieveLevel=STUDY -k PatientID=SYN0001 -k StudyInstanceUID -k NumberOfStudyRelatedInstances <B的IP> 11112
movescu -S -aet <你的AE> -aem <你的AE> -aec PACS_B --port <你的存储端口> -od out -k QueryRetrieveLevel=STUDY -k StudyInstanceUID=<UID> <B的IP> 11112
getscu  -S -aet <你的AE> -aec PACS_B -od out -k QueryRetrieveLevel=STUDY -k StudyInstanceUID=<UID> <B的IP> 11112
```

## 9. 已知限制

- 没有 TLS，AE + IP 可被伪造。
- 不支持关系查询。
- 不支持 Storage Commitment。
- 不转码存储。
- 压缩传输语法和其余 117 个存储类只做了声明（E0）。
- 中文姓名只验证了 ISO_IR 192，未验证 GB18030。
- 单机部署，没有高可用。

完整列表见 `../known-issues.md`。
