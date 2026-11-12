# Design

## Context

- 项目为空。技术栈遵循课程基线：Python、pydicom、pynetdicom、PyQt5、SQLite、NumPy。实际版本：pydicom 3.0.2、pynetdicom 3.0.4、PyQt5 5.15.11 / Qt 5.15.2、Python 3.12.14，`uv.lock` 锁定。
- 运行环境为 Windows 11。只使用本机回环对端和合成数据。跨组 A / C 真实对端尚不存在。
- 需求来源与讨论见 `../../../docs/requirements-discussion/`。需求编号 R1–R21 与 specs 中一致。所有默认假设都是“实验基线假设，未经医院客户确认”。
- 已在 pynetdicom 3.0.4 源码中核实的行为：
  - `Event.encoded_dataset(include_meta=False)` 返回收到的原始数据集字节；`include_meta=True` 时的 File Meta 由 SCP 生成。
  - ACSE `_negotiate_as_acceptor` 中的拒绝路径为 `send_reject(result, source, diag)`；超出关联上限时为 (2,3,2)。
  - C-MOVE 若目的地为 `(None, None)`，返回 0xA801，且在发起外连之前就返回。
  - Q/R 子操作中 `send_c_store` 抛出的异常计为失败子操作，并把实例 UID 加入 Failed SOP Instance UID List。

## Goals / Non-Goals

**Goals:**
- 用真实 DIMSE 实现 C-ECHO / C-STORE / C-FIND / C-MOVE / C-GET SCP，第三方 SCU 只看接口文档即可对接。
- 白名单准入在关联层生效，在线维护后立即生效。
- “成功”具有持久化语义；重复与冲突的判定可预测。
- 基线、核查、告警、审计形成闭环；正常业务操作不产生误报。
- 每条需求都有可执行的正反用例，测试报告按“编号 / 目标 / 输入 / 期望 / 实际 / 结论”输出。

**Non-Goals:**
- TLS、GUI 登录 / RBAC、Storage Commitment、MWL / MPPS / WADO / DICOMweb / HL7、转码存储、删除 / 更正 / 合并、高可用 / 备份、诊断阅片、跨主机部署。

## Decisions

### D-A 架构（单进程服务核心 + 可选 GUI）

```
                 +-------------------------- PacsService (src/pacs_server/service.py) ----------------------+
 A 组/设备 ----> | DicomServer (netserver.py)                                                               |
 (C-STORE)       |   PacsRequestHandler -> PacsACSE: 白名单准入(AE+IP) + 按角色过滤表示上下文 -> RJ/AC       |
                 |   handlers: ECHO | STORE -> ImageStore.ingest | FIND -> QueryEngine | MOVE/GET -> Retrieve |
 C 组/工作站 <-> |                                                                                          |
 (FIND/MOVE/GET) | ImageStore (store.py)   Verifier (integrity.py)   Whitelist (whitelist.py)               |
                 | AlertManager (alerts.py)   AuditLog (audit.py)   Database (db.py, SQLite WAL)             |
                 +------------------------------------------------------------------------------------------+
                        |                 |                     |                         |
               runtime/data/store   runtime/data/pacs.db   runtime/keys/hmac.key   runtime/anchors/audit-anchor.log
                        ^
   CLI (cli.py) ------- + ------- PyQt5 控制台 (gui/app.py，可内嵌启动服务)
```

### D-B 数据流（C-STORE 入库）

```
SCU --A-ASSOCIATE-RQ--> PacsACSE --[白名单不符]--> A-ASSOCIATE-RJ(1,1,3) + 审计 + 告警
                          |--[通过]--> 按角色过滤上下文 --> pynetdicom 协商 --> A-ASSOCIATE-AC
SCU --P-DATA(C-STORE)--> handle_store(event)
   raw = event.encoded_dataset(include_meta=False)      # 网络数据集字节（基线对象）
   ImageStore.ingest(raw, TS, 请求的 SOP 类/实例 UID, 对端)
     1 按 UID 加锁
     2 解析 + 校验（缺 UID -> C001；UID 不一致 -> C002/A900；无法解析 -> C004）
     3 已存在? -> 校验已存副本（损坏 -> C011） -> 相同/等价 -> 0000；不同 -> C010（隔离）
     4 计算基线（文件/数据集/像素哈希、元数据快照、分块哈希）
     5 写 tmp -> fsync -> 读回比对 -> os.replace         （失败 -> A700）
     0 审计锚点降级中? -> 先尝试补写缺失锚点；仍失败 -> 0xA701（不写文件、不写索引）
     6 BEGIN IMMEDIATE: 层级校验(C003) + 索引 + 基线(HMAC) + 审计 -> COMMIT（失败 -> C0FF，删除文件）
     7 追加审计锚点（anchor_safely）-> 成功: 返回 0x0000
                                   -> 失败: 补偿事务撤销本次索引/基线/新建层级行 + STORE_ROLLED_BACK
                                            -> 删除文件 -> 0xA701（见 D-L）
```

### D-C 数据流（C-MOVE / C-GET 取回）

```
SCU C-MOVE(identifier, MoveDestination)
  -> 角色复核 -> 目的地只从白名单(MOVE_DEST, 启用)取 host:port；否则 yield (None,None) => 0xA801 + 告警（无外连）
  -> QueryEngine.resolve_retrieve(level, 唯一键) -> 实例列表 -> yield 数量
  -> 逐实例：读文件 -> Verifier.check_quick(文件哈希 + 基线 HMAC)
        通过: yield (0xFF00, Dataset)
        不通过(默认 block): yield (0xFF00, 占位 Dataset[只有 SOPClass/SOPInstance UID，无 File Meta])
              -> pynetdicom 在发送前抛异常 -> 计为失败子操作，UID 进入 Failed SOP Instance UID List
              -> RETRIEVE_BLOCKED 告警
     is_cancelled -> yield (0xFE00, None)
  -> pynetdicom 汇总：0x0000 / 0xB000 / 0xA702 / 0xFE00
```

### D-D 模块接口（函数签名级）

| 模块 | 主要接口 |
|---|---|
| `config.PacsConfig` | `load(runtime_dir: Path) -> PacsConfig`; `save()`; 字段 `ae_title, host, port, max_associations, retrieve_policy, periodic_scan_minutes, read_back_verify, acse_timeout, dimse_timeout, network_timeout, max_pdu` |
| `db.Database` | `write() -> ContextManager[sqlite3.Connection]`（进程级锁 + BEGIN IMMEDIATE）；`query(sql, params) -> list[Row]`；`query_one(...)` |
| `audit.AuditLog` | `append(event_type, *, actor, actor_ip, target, outcome, details, conn=None) -> int`；`verify(expect_head: str \| None) -> AuditReport`；`head() -> (seq, hash)`；`export(path, operator)` |
| `alerts.AlertManager` | `raise_alert(type, severity, subject, details, suggestion, aggregate_key=None, window_s=None) -> int`；`ack(alert_id, operator)`；`list(status=None)` |
| `whitelist.Whitelist` | `add(ae, host, port, roles, description, operator)`；`update(ae, operator, **fields)`；`remove(ae, operator)`；`admit(calling_ae, called_ae, ip) -> Admission`；`validate_ae_title(s)` |
| `store.ImageStore` | `ingest(raw: bytes, transfer_syntax: str, req_sop_class: str, req_sop_uid: str, peer: PeerInfo) -> IngestResult(status, comment, outcome)`；`reconcile() -> list[str]` |
| `integrity.Verifier` | `compute_baseline(raw, ds, file_bytes) -> Baseline`；`verify_instance(instance_row) -> list[Finding]`；`run(scope, trigger, operator) -> VerificationReport`；`check_for_retrieve(instance_row) -> list[Finding]` |
| `query.QueryEngine` | `find(model: 'P'\|'S', identifier: Dataset) -> Iterator[Dataset]`（异常 `QueryError(status)`）；`resolve_retrieve(model, identifier) -> list[InstanceRow]` |
| `netserver.DicomServer` | `start()`；`stop()`；`active_associations()` |
| `service.PacsService` | `start()`；`stop()`；`status() -> dict`；并持有上述各组件 |

### D-E DICOM IOD 与服务类选型（理由）

| 服务 / IOD | UID | 理由 |
|---|---|---|
| Verification | 1.2.840.10008.1.1 | 课程强调“确认连通”；设备和工作站调试的第一步 |
| CT Image Storage | 1.2.840.10008.5.1.4.1.1.2 | 客户点名 CT；C 组以 CT 为主 |
| VL Endoscopic Image Storage | 1.2.840.10008.5.1.4.1.1.77.1.1 | 客户点名内镜；A 组为内镜采集 |
| Secondary Capture Image Storage | 1.2.840.10008.5.1.4.1.1.7 | A 组可能用 SC 封装采集画面（Q-A1 待确认） |
| 其他常用存储类（MR、CR、DX、US、Enhanced CT 等，取 pynetdicom `StoragePresentationContexts`） | — | “全院影像设备”：原样保存，不解析像素语义；证据级别 E0，只声明、未逐类实测 |
| Patient Root / Study Root Q/R FIND、MOVE、GET | 1.2.840.10008.5.1.4.1.2.{1,2}.{1,2,3} | C 组可能任选其一；同时支持可提高对接成功率。Patient Root 对应“按患者组织”，Study Root 为工作站常用模型 |
| 不支持：Storage Commitment Push | 1.2.840.10008.1.20.1 | 本期不做；明确拒绝，避免对端把 C-STORE 成功误认为存储承诺 |

传输语法：Implicit VR LE、Explicit VR LE、Explicit VR BE（兼容）、JPEG Baseline、JPEG Lossless SV1、JPEG-LS Lossless、JPEG 2000（无损 / 有损）、RLE Lossless。一律原样保存、不转码。未列出的语法（如 Deflated）协商时被拒。

### D-F C-STORE 实现方式对比与取舍

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| a 解码后重存 | `event.dataset` + `save_as` | 简单 | pydicom 重编码可能改变字节（长度、VR、填充），破坏“与上传时一致” | 否 |
| b 原样字节（采用） | `encoded_dataset(include_meta=False)`，前面加服务器生成的 File Meta | 字节保真；`dataset_sha256` 与发送端可直接比对 | 需要自己生成 File Meta；压缩数据不能预览 | **是** |
| c 规范化存储 | 统一转为 Explicit VR LE | 方便显示 | 违反字节一致；压缩数据无法转换 | 否 |
| d 分块落盘（`STORE_RECV_CHUNKED_DATASET`） | pynetdicom 直接写临时文件 | 省内存 | 与读回校验、原子提交的组合更复杂；原型数据量小 | 暂不采用（可在需要处理大对象时切换） |

### D-G 准入实现

pynetdicom 自带的 `require_calling_aet` 是静态列表，不含 IP。采用 `PacsRequestHandler._create_association` 把 ACSE 替换为 `PacsACSE` 子类：`_negotiate_as_acceptor` 先做白名单判定，不通过就 `send_reject(1,1,3|7)`；通过后按角色裁剪 `acceptor.supported_contexts`，再交给父类协商（包括父类的关联上限检查）。这依赖 pynetdicom 的内部方法，已锁定版本 3.0.4；升级时必须重跑 TC-R2 / TC-R3。

### D-H 完整性与审计的信任边界

- 基线行 HMAC：`HMAC(key, file_sha256|dataset_sha256|pixel_sha256|meta_sha256|sop_uid|rel_path)`。
- 审计：`entry_hash = SHA256(prev_hash + canonical_json(fields))`，`hmac = HMAC(key, entry_hash)`。事务提交后追加锚点行 `seq\tentry_hash\thmac`。
- 能检测：单方面改动文件、数据库索引、基线或审计中间记录；只截断数据库尾部；带外链头不在当前链中。
- 不能检测：同时持有密钥与写权限的重算攻击；数据库与锚点同时被截断、又没有带外链头可比对；入库前就已发生的变化；隔离区内文件的变化。

### D-I 状态码

完整列表见 `docs/interface/dicom-conformance-statement.md`。C-STORE 的服务器自定义失败码放在 PS3.4 规定的失败类 Cxxx（Cannot Understand）区段：C001 缺属性、C002 UID 不一致、C003 层级冲突、C004 无法解析、C010 UID 冲突、C011 已存副本损坏、C0FF 内部事务失败；另有 A700（落盘失败）、A701（审计锚点不可写，本次未入库）、A900（SOP 类不符）。

### D-L 审计锚点写入失败的提交语义（独立复核后修复）

Codex 复现：入库事务提交后令锚点写入抛 OSError，旧实现进入通用 except，删除已写文件，失败审计再次触发锚点异常，处理器返回 0xC211，留下“索引 1 行、文件 0 个”。修复后的语义：

1. **成功 = 已持久化 + 已建立基线 + 审计已锚定**。0x0000 只在锚点写入成功后返回。
2. **COMMIT 是唯一的提交点**。提交前失败才删除文件；提交后任何路径都不会在不撤销索引的情况下删除文件。
3. **提交后锚点失败 → 补偿撤销**。新事务删除本次的基线和实例行，以及本次新建且已无子项的序列 / 检查 / 患者行（仍有子项的保留，并重设 origin），同时写 STORE_ROLLED_BACK（引用 STORE_ACCEPTED 的 seq）。补偿提交后才删文件，然后返回 0xA701。若补偿事务本身也失败，就保留文件和索引（二者一致），返回 0xA701 并在告警中写明“已入库，锚点待补”，恢复后重发会走幂等成功路径。
4. **锚点降级状态**。`AuditLog.anchor_safely()` 捕获写入异常，绝不向调用方抛出；首次失败时进入降级并回调告警 AUDIT_ANCHOR_FAILURE，回调中的嵌套失败不会再次触发，从而消除递归异常。降级期间：
   - 审计照常写入数据库（链与 HMAC 不变），只是暂未锚定；
   - C-STORE 在落盘前被预先拒绝，返回 0xA701；
   - ECHO / FIND / MOVE / GET、白名单维护、核查照常进行。
5. **恢复**。每次入库预检和每次审计校验都会尝试 `recover_anchors()`，把数据库中所有未锚定的 seq 补写进锚点文件；成功后解除降级，追加 AUDIT_ANCHOR_RECOVERED（含补写条数）。`verify()` 的“未锚定条数”改为按 seq 逐一统计。
6. **边界**。降级窗口内的记录在补写前没有锚点保护；窗口内的尾部截断只能靠带外链头发现。

### D-M 基线不可信时的核查降级（独立复核后修复）

Codex 复现：把 baselines.meta_json 改成“{”后，`verifier.run` 抛出 JSONDecodeError 而中断，BASELINE_TAMPERED 告警数为 0。修复：

- 基线视为不可信输入：`load_baseline_meta` / `load_baseline_tiles` 校验 JSON 格式与结构，不合格时返回原因，不抛异常。
- 基线行缺失 → BASELINE_TAMPERED（基线记录缺失），只检查文件是否存在。
- HMAC 不符或 JSON 无效 → BASELINE_TAMPERED（列出问题），其余比对降级进行：
  - 像素哈希列仍可用 → 报告 PIXEL_CHANGED，但不给分块位置并注明原因；
  - 元数据哈希列仍可用 → 报告 METADATA_CHANGED，但不给前值；
  - 快照不可用 → 跳过索引比对。
- 单实例隔离：`verify_instance()` 包住 `_verify_instance()`，残余的意外异常记为 VERIFY_ERROR 并告警，不中断批量核查、C-MOVE / C-GET 的调阅前校验和重发判定。
- 调阅时：基线缺失或 HMAC 不符的实例 quick_check 不通过，按 R16 阻断并计入失败子操作（TC-R16-N2）。

### D-J 并发

pynetdicom 为每个关联开一个线程。按 SOP Instance UID 加锁，使同 UID 的处理串行；数据库写操作用进程级锁加 `BEGIN IMMEDIATE`，事务尽量短；文件 I/O 与哈希计算放在锁外。SQLite 使用 WAL，GUI 另开进程也能并发读。

### D-K 模拟与测试

- `sim/synth.py` 生成合成 CT / 内镜 / SC。
- `sim/scu.py` 是基于 pynetdicom 的设备与工作站模拟器（真实 TCP）。
- `sim/tamper.py` 是篡改注入工具（仅用于演示和测试）。
- 测试用 pytest，每个用例的 docstring 写 “编号 | 目标 | 输入 | 期望”，实际观测值用 `record_property` 记录，`scripts/make_test_report.py` 由此生成报告。
- 如有 DCMTK（`work/tools/dcmtk`），再做独立实现的互操作测试。

## Risks / Trade-offs

- [依赖 pynetdicom 内部 API（ACSE 子类）] → 版本锁定在 3.0.4，测试覆盖准入路径；升级前必须重跑测试。
- [占位数据集触发子操作失败，依赖 pynetdicom 的异常计数行为] → 有专门测试 TC-R16-N1 断言失败计数与失败列表。
- [0xA801 同时表示“未登记”和“连不上”] → 审计区分两种原因，并写入接口文档。
- [Windows fsync 拖慢并发吞吐] → 并发阈值设得宽松（300 秒），并记录实测值。
- [同库两端互测证据弱] → 尽量补 DCMTK 测试；拿不到时证据级别如实标为 E2。
- [GUI 只做了离屏冒烟] → 真人验收（H）标为未执行，留给用户。
- [密钥与数据同机] → 在边界中写明；带外链头作为补充手段。
- [锚点降级期间入库暂停] → 以可用性换取“成功必有完整记录”；AUDIT_ANCHOR_FAILURE 告警提示管理员，恢复后自动补锚（D-L）。
