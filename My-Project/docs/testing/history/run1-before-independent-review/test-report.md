# 测试报告（自动生成）

- 生成时间：2026-11-12T15:08:54-08:00
- 来源：`docs/testing/logs/junit.xml`（pytest junit XML），原始日志见 `docs/testing/logs/`
- 环境：python 3.12.14；pydicom 3.0.2；pynetdicom 3.0.4；numpy 2.5.4；PyQt5 5.15.11 (Qt 5.15.2)；DCMTK dcmtk: echoscu v3.7.1 2026-10-15
- 结果：共 85 个用例；通过 85

**证据级别**：E1 = 单元 / 核心函数；E2 = pynetdicom 真实 TCP 回环（同一库两端）；E3 = DCMTK 3.7.1 独立实现回环；E4 = PyQt5 离屏自动化冒烟。**H（真人操作验收）与 X（跨组跨主机联调）均未执行**，本报告不包含这两类结论。

“实际”一列是测试运行时程序记录的观测值；“结论”取自 pytest 的判定。所有数据都是合成数据，对端都运行在 127.0.0.1。

## 1. 系统测试（按需求编号）

| 编号 | 目标 | 输入 | 期望 | 实际 | 结论 | 证据 |
|---|---|---|---|---|---|---|
| TC-R1-N1 | 被叫 AE 错误被拒 | WS_SIM_1 以被叫 AE WRONG_AE 请求关联 | A-ASSOCIATE-RJ (1,1,7)，审计 ASSOCIATION_REJECTED | rejected=True rj=(1,1,7) | 通过 | E2 |
| TC-R1-P1 | 已登记工作站 C-ECHO 成功 | WS_SIM_1@127.0.0.1 以被叫 AE PACS_B 发 C-ECHO | 状态 0x0000，审计有 DICOM_ECHO | established=True statuses=['0x0']；审计含 DICOM_ECHO | 通过 | E2 |
| TC-R2-N1 | 未登记 AE 被拒并告警 | ROGUE 请求关联 | RJ(1,1,3)，告警 UNREGISTERED_PEER | rj=(1,1,3)；UNREGISTERED_PEER 告警数=1 | 通过 | E2 |
| TC-R2-N2 | AE 已登记但 IP 不符被拒 | 登记为 10.0.0.5 的 CT_SPOOF 从 127.0.0.1 请求关联 | RJ(1,1,3)，告警 PEER_ADDRESS_MISMATCH | rj=(1,1,3)；PEER_ADDRESS_MISMATCH 告警 1 条 | 通过 | E2 |
| TC-R2-N3 | 停用条目被拒 | 停用 CT_SIM_2 后请求关联 | RJ(1,1,3)，告警 PEER_DISABLED | rj=(1,1,3) | 通过 | E2 |
| TC-R2-P1 | 登记设备被接受 | CT_SIM_1@127.0.0.1（已登记）请求关联 | 关联建立 | established=True | 通过 | E2 |
| TC-R3-N1 | 只有 STORE 角色的设备不能取回 | CT_SIM_1 提议 Study Root MOVE 上下文 | 该上下文被拒，告警 UNAUTHORIZED_RETRIEVE | established=True accepted=['1.2.840.10008.1.1'] rejected=['1.2.840.10008.5.1.4.1.2.2.2']；UNAUTHORIZED_RETRIEVE 告警 1 条 | 通过 | E2 |
| TC-R3-N2 | 只有 STORE 角色的设备不能查询 | CT_SIM_1 提议 Study Root FIND 上下文 | 上下文被拒，审计 CONTEXT_DENIED | rejected=['1.2.840.10008.5.1.4.1.2.2.1'] | 通过 | E2 |
| TC-R3-P1 | 具备 QUERY 角色的工作站可 C-FIND | WS_SIM_1 提议 Study Root FIND 并查询 STUDY 级 | 上下文被接受，最终状态 0x0000 | established=True matches=1 final=0x0000 | 通过 | E2 |
| TC-R4-N1[   ] | 非法 AE Title 被拒绝 | 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 抛出 WhitelistError | '   ' -> AE Title 不能为空或全为空格 | 通过 | E1 |
| TC-R4-N1[AAAAAAAAAAAAAAAAA] | 非法 AE Title 被拒绝 | 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 抛出 WhitelistError | 'AAAAAAAAAAAAAAAAA' -> AE Title 超过 16 个字符：17 | 通过 | E1 |
| TC-R4-N1[BAD\\AE] | 非法 AE Title 被拒绝 | 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 抛出 WhitelistError | 'BAD\\AE' -> AE Title 不能包含反斜杠 | 通过 | E1 |
| TC-R4-N1[TAB\tAE] | 非法 AE Title 被拒绝 | 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 抛出 WhitelistError | 'TAB\tAE' -> AE Title 只能包含可打印 ASCII 字符（不含控制字符） | 通过 | E1 |
| TC-R4-N1[\u4e2d\u6587AE] | 非法 AE Title 被拒绝 | 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 抛出 WhitelistError | '中文AE' -> AE Title 只能包含可打印 ASCII 字符（不含控制字符） | 通过 | E1 |
| TC-R4-N1[] | 非法 AE Title 被拒绝 | 17 字符 / 含反斜杠 / 空 / 全空格 / 控制字符 / 非 ASCII | 抛出 WhitelistError | '' -> AE Title 不能为空或全为空格 | 通过 | E1 |
| TC-R4-N1b | 非法白名单条目不保存 | 重复 AE、IP 999.1.1.1、端口 70000、未知角色、MOVE_DEST 无端口 | 全部拒绝，白名单只有最初 1 条 | AE Title 已存在：WS_A；IP 地址不合法：'999.1.1.1'（本期只接受精确 IPv4/IPv6 地址）；端口超出范围 1–65535：70000；未知角色：ADMIN（可选：STORE, QUERY, RETRIEVE, MOVE_DEST）；具有 MOVE_DEST 角色的条目必须登记端口 | 通过 | E1 |
| TC-R4-N2 | 停用后新关联被拒 | 停用 CT_SIM_1 后再次请求关联 | RJ(1,1,3)，审计 PEER_UPDATED 含 enabled 前后值 | rj=(1,1,3) before.enabled=True after.enabled=False | 通过 | E2 |
| TC-R4-P1 | 新增条目立即生效 | 运行中新增 ENDO_SIM_3（STORE），随后发送 C-STORE | 不重启即 0x0000，审计 PEER_ADDED | 新增前 rejected=True；新增后 status=['0x0'] | 通过 | E2 |
| TC-R5-N1 | 落盘失败返回 0xA700 | 故障注入 fail_write，CT_SIM_1 发送 1 张 CT | 0xA700，库中无记录，存储目录与 tmp 无残留，告警 STORAGE_FAILURE | status=['0xa700'] instances=0 store_files=0 tmp=0 | 通过 | E2 |
| TC-R5-N2 | 事务失败返回 0xC0FF 并清理文件 | 故障注入 fail_db，CT_SIM_1 发送 1 张 CT | 0xC0FF，库中无记录，已写文件被删除 | status=['0xc0ff'] instances=0 store_files=0；故障解除后重发 0x0000 | 通过 | E2 |
| TC-R5-P1 | 成功入库且数据集字节与发送端一致 | CT_SIM_1 以 Explicit VR LE 发送 1 张合成 CT（按文件原样字节） | 0x0000；库中 1 条；dataset_sha256 = 发送端文件 File Meta 之后字节的 SHA-256；库内 File Meta 的 ImplementationClassUID 是 B 的 | status=['0x0'] instances=1；sender_dataset_sha256=15849eb616e90065… stored dataset_sha256=15849eb616e90065…；stored ImplementationClassUID=2.25.168856098296771086472905059958299090808；sender=1.2.826.0.1.3680043.8.498.1 | 通过 | E2 |
| TC-R5-P2 | 内镜（VL Endoscopic）与二次捕获（SC）入库 | ENDO_SIM_1 发送 2 张 RGB 内镜与 2 张 SC | 全部 0x0000，SOP 类与模态正确，dataset_sha256 与发送端一致 | status=['0x0', '0x0', '0x0', '0x0'] classes=[('1.2.840.10008.5.1.4.1.1.7', 'OT'), ('1.2.840.10008.5.1.4.1.1.77.1.1', 'ES')] hash_equal=True | 通过 | E2 |
| TC-R6-N1 | 缺少 SeriesInstanceUID 被拒 | 删除 SeriesInstanceUID 的数据集 | 0xC001，ErrorComment 含 SeriesInstanceUID，不入库 | status=0xC001 comment='missing SeriesInstanceUID' instances=0 | 通过 | E2 |
| TC-R6-N2 | SOP Instance UID 与请求不一致被拒 | C-STORE 请求的 Affected SOP Instance UID 与数据集 SOPInstanceUID 不同 | 0xC002，不入库 | status=0xC002 comment='SOPInstanceUID differs from request' | 通过 | E2 |
| TC-R6-N3 | Series 归属冲突被拒 | 实例的 SeriesInstanceUID 已属于另一个 StudyInstanceUID | 0xC003，原有层级不变 | status=0xC003 comment='Series belongs to another Study' studies=1 | 通过 | E2 |
| TC-R6-P1 | 完整数据集入库 | CT_SIM_1 发送 5 张属性完整的合成 CT | 全部 0x0000 | status=['0x0', '0x0', '0x0', '0x0', '0x0'] | 通过 | E2 |
| TC-R7-N1 | 同 UID 不同像素被拒并隔离 | 发送与已存实例同 UID、像素不同的数据集 | 0xC010，已存文件与基线不变，隔离区 +1，告警 UID_CONFLICT | status=0xC010 quarantine=1 file_unchanged=True baseline_unchanged=True | 通过 | E2 |
| TC-R7-N2 | 已存副本损坏时重发不报成功 | 篡改已存文件像素后，设备重发与原基线相同的数据 | 0xC011，告警 STORED_COPY_DAMAGED，库内文件不被自动覆盖 | status=['0xc011'] STORED_COPY_DAMAGED=1 file_still_tampered=True | 通过 | E2 |
| TC-R7-P1 | 相同实例重发幂等成功 | 同一实例同一字节发送两次 | 两次 0x0000，库中 1 条，审计 STORE_DUPLICATE_IDENTICAL，告警 0 | s1=[0] s2=[0] instances=1 dup_audit=1 alerts=0 | 通过 | E2 |
| TC-R7-P2 | 不同编码的同内容重发视为等价 | 先以 Explicit VR LE 入库，再以 Implicit VR LE 重发同一实例 | 0x0000，库中 1 条，已存文件不变，审计 STORE_DUPLICATE_EQUIVALENT | status=[(0, '')] instances=1 file_unchanged=True equivalent_audit=1 | 通过 | E2 |
| TC-R8-N1 | 不同 PatientID 同姓名不合并 | SYN0003 与 SYN0004 均名为 TEST^SAME 的实例入库 | PATIENT 级查询 TEST^SAME 返回 2 个患者 | patients=['SYN0003', 'SYN0004'] | 通过 | E2 |
| TC-R8-N2 | 人口学不一致只标记不告警 | PatientID=SYN0001 但姓名不同的实例入库 | 入库成功，该患者 demographics_conflict=1，告警数不增加 | status=['0x0'] demographics_conflict=1 alerts=0 | 通过 | E2 |
| TC-R8-N3 | PatientID 为空时归入空 ID 患者并审计注明 | PatientID 为空（Type 2）的 CT 入库 | 0x0000，患者表中有 patient_id='' 的记录，STORE_ACCEPTED 审计含“PatientID 为空” | status=['0x0'] empty_id_patients=1 note='PatientID 为空，归入空 ID 患者' | 通过 | E2 |
| TC-R8-P1 | 同一患者的两个检查归在一起 | SYN0001 的 CT 检查与内镜检查入库 | PATIENT 级 C-FIND 返回 1 个患者，NumberOfPatientRelatedStudies=2 | final=0x0000 patients=1 studies=['2'] | 通过 | E2 |
| TC-R9-N1 | 无匹配时返回空结果且成功 | PatientID=NOPE | 没有 0xFF00 响应，最终 0x0000 | matches=0 final=0x0000 | 通过 | E2 |
| TC-R9-N2 | 缺少上层唯一键被拒 | Study Root SERIES 级查询不带 StudyInstanceUID | 最终 0xA900 | matches=0 final=0xA900 | 通过 | E2 |
| TC-R9-N3 | 查询中途取消 | IMAGE 级查询（5 条匹配，测试节流 0.2 s/条），收到第 1 条后发 C-CANCEL | 最终 0xFE00，收到少于 5 条 | received=1 final=0xFE00 | 通过 | E2 |
| TC-R9-P1 | 按 PatientID 查询检查 | Study Root STUDY 级 PatientID=SYN0001 | 恰好返回 SYN0001 的 2 个检查，含 StudyInstanceUID/StudyDate/ModalitiesInStudy/NumberOfStudyRelatedInstances，最终 0x0000 | final=0x0000 results=[('20261105', 'CT', 5), ('20261108', 'ES', 4)] | 通过 | E2 |
| TC-R9-P2 | 通配符、日期范围与中文姓名匹配 | PatientName=TEST*；StudyDate=20261101-20261130；PatientName=测试^患者二（ISO_IR 192） | 分别返回 SYN0001 的 2 个检查、11 月的 2 个检查、SYN0002 的 1 个检查 | TEST*=2 日期范围=2 中文=[('SYN0002', '测试^患者二')] | 通过 | E2 |
| TC-R10-N1 | 未登记目的地被拒且不外连 | WS_SIM_1 请求 C-MOVE 到未登记的 UNKNOWN_WS | 最终 0xA801，服务器未调用出站 associate，告警 UNKNOWN_MOVE_DESTINATION | final=0xA801 outbound_associate_calls=0 alerts=1 | 通过 | E2 |
| TC-R10-N2 | 目的地停用后被拒 | 停用 WS_SIM_2 后，WS_SIM_1 请求 C-MOVE 到 WS_SIM_2 | 最终 0xA801，WS_SIM_2 未收到影像，告警 UNKNOWN_MOVE_DESTINATION | final=0xA801 ws2_received=0 | 通过 | E2 |
| TC-R10-P1 | 推送整个检查到已登记工作站 | 100 张（128×128）CT 检查，WS_SIM_1 请求 C-MOVE 到 WS_SIM_1 | 最终 0x0000，完成 100、失败 0；收到的每个数据集哈希等于基线 dataset_sha256 | final=0x0000 completed=100 failed=0 received=100 hash_mismatch=0 | 通过 | E2 |
| TC-R11-N1 | 未协商存储 SCP 角色时子操作失败 | WS_SIM_2 C-GET SYN0001 的 CT 检查，但不为 CT Image Storage 协商 SCP 角色 | 最终 0xA702，失败数 = 5，Failed SOP Instance UID List 含这 5 个实例 | final=0xA702 failed=5 failed_uids=5 | 通过 | E2 |
| TC-R11-N2 | C-GET 取回中途取消 | 10 张 CT（测试节流 0.2 s/张），收到第 1 个子操作后发 C-CANCEL | 最终 0xFE00，完成数 < 10 | final=0xFE00 completed=1 | 通过 | E2 |
| TC-R11-P1 | C-GET 取回整个序列 | 25 张内镜序列，WS_SIM_2 SERIES 级 C-GET | 最终 0x0000，完成 25，收到的数据集哈希等于基线 | final=0x0000 completed=25 received=25 hash_mismatch=0 | 通过 | E2 |
| TC-CC1-P1 | 并发接收与并发取回 | CT_SIM_1/2 各 100 张 512×512 CT、ENDO_SIM_1/2 各 25 张 640×480 内镜同时发送；同时 WS_SIM_1 做 3 轮 C-FIND+C-MOVE、WS_SIM_2 做 3 轮 C-FIND+C-GET（对象为预置的 100 张检查） | 250 个 C-STORE 全 0x0000，库中新增 250；每轮取回完成 100 失败 0 且哈希一致；总耗时 ≤ 300 s 并记录实际值 | elapsed=7.9s added=250 all_store_success=True spot_hash_ok=True echo_during_load=[0] latency=0.14s；WS_SIM_1(find,n,final,completed,failed,hash_bad)=[(0, 100, 0, 100, 0, 0), (0, 100, 0, 100, 0, 0), (0, 100, 0, 100, 0, 0)]；WS_SIM_2=[(0, 100, 0, 100, 0, 0), (0, 100, 0, 100, 0, 0), (0, 100, 0, 100, 0, 0)] errors=[] alerts=0 | 通过 | E2 |
| TC-CC3-N1 | 超过关联上限被临时拒绝 | max_associations=8，同时保持 9 个关联 | 至少 1 个收到 A-ASSOCIATE-RJ (2,3,2)；已建立的关联仍能 C-ECHO；释放后重试成功；拒绝被审计 | held=8 rejected=[(2, 3, 2)] echo_on_held=[0] retry=[0] rj_audit=1 | 通过 | E2 |
| TC-CC4-N1 | 同 UID 并发竞争 | (a) 内容相同；(b) 内容不同 | (a) 两者成功、库中 1 条；(b) 恰好 1 个 0x0000、1 个 0xC010，库中 1 条 | 相同内容={'CT_SIM_1': 0, 'CT_SIM_2': 0} 库中=1；不同内容={'CT_SIM_1': 0, 'CT_SIM_2': 49168} 库中=1 | 通过 | E2 |
| TC-R13-N1 | 中断时未完成的实例不可见 | 512×512 CT 以 4 KB PDU 发送 3 个 P-DATA 后 A-ABORT | 库中无该实例、存储与 tmp 目录无半截文件、C-FIND 查不到、审计有 ASSOCIATION_ABORTED、告警 0 | abort=[('aborted-after-pdus', 3)] instances=0 files=0 find=0 aborted_audit=1 alerts=0 | 通过 | E2 |
| TC-R13-P1 | 传输中断后重发成功且无告警 | 同上中断后，设备重连并重发同一实例 | 重发 0x0000，库中 1 条，告警 0 | 重发 status=['0x0'] instances=1 alerts=0 | 通过 | E2 |
| TC-R14-N1 | 基线记录被改能检出 | 直接在数据库中修改该实例基线的 file_sha256 | 核查结果含 BASELINE_TAMPERED 并产生告警 | categories=['BASELINE_TAMPERED'] alerts=1 | 通过 | E2 |
| TC-R14-P1 | 基线可复算 | 一张 CT 入库后立即核查该实例 | 文件/数据集/像素哈希与基线一致、HMAC 通过、结果 OK | checked=1 ok=1 findings=[] hmac_ok=True | 通过 | E2 |
| TC-R15-N1 | 像素被改动 | 128×128 CT 像素区第 16640 字节（第 65 行附近）翻转 | PIXEL_CHANGED，给出帧 1、行 64–95、列 0–31 的变化区域 | categories=['PIXEL_CHANGED'] regions=[{'frame': 1, 'tiles_changed': 1, 'row_range': [64, 95], 'col_range': [0, 31]}] other_instances_ok=4 | 通过 | E2 |
| TC-R15-N2 | 检查日期被改动 | 文件 StudyDate 20261105 → 20261106 | METADATA_CHANGED，字段 (0008,0020) StudyDate，前 20261105 后 20261106，关键字段 | categories=['METADATA_CHANGED'] change={'tag': '(0008,0020)', 'keyword': 'StudyDate', 'key_field': True, 'before': '20261105', 'after': '20261106'} | 通过 | E2 |
| TC-R15-N3 | 文件被删除 | 删除一个库内文件后核查 | FILE_MISSING | categories=['FILE_MISSING'] | 通过 | E2 |
| TC-R15-N4 | 只改数据库索引、文件看似正常也要报警 | 仅把数据库中 SYN0001 的 PatientName 改为 FAKE^NAME | INDEX_MISMATCH，给出索引值 FAKE^NAME 与基线值 TEST^PATIENT01 | categories=['INDEX_MISMATCH'] diffs=[{'field': 'patients.patient_name', 'tag': '(0010,0010)', 'index_value': 'FAKE^NAME', 'baseline_value': 'TEST^PATIENT01'}] | 通过 | E2 |
| TC-R15-P1 | 正常业务操作后无误报 | 多次 C-MOVE / C-GET、相同实例重发、服务重启、白名单变更后全量核查 | 异常 0、新告警 0、库内文件 SHA-256 与核查前一致 | checked=5 anomalies=0 alerts=0 files_unchanged=True | 通过 | E2 |
| TC-R16-N1 | 被篡改的实例被阻断 | 检查中 1 张像素被改后执行 C-MOVE | 该实例未发送；最终 0xB000，失败 1，Failed SOP Instance UID List 含该实例；告警 RETRIEVE_BLOCKED | final=0xB000 completed=4 failed=1 failed_uids=['1.2.826.0.1.3680043.8.498.73078168394838122386686785566274593909'] received_tampered=False | 通过 | E2 |
| TC-R16-P1 | 未改动的实例正常发送 | 对未改动检查执行 C-MOVE | 全部发送成功，没有 RETRIEVE_BLOCKED | final=0x0000 completed=5 blocked_alerts=0 | 通过 | E2 |
| TC-R16-P2 | 无损传输语法转换不告警 | 库内 Explicit VR LE 实例被只接受 Implicit VR LE 的工作站 C-GET | 成功；收到 Implicit 编码；像素字节哈希 = 基线 pixel_sha256；库内文件不变；告警 0 | final=0x0000 completed=5 ts=['1.2.840.10008.1.2'] pixel_equal=True files_unchanged=True alerts=0 | 通过 | E2 |
| TC-R17-N1 | 异常产生告警且同类聚合、确认留痕 | ROGUE 连续 3 次尝试关联，随后管理员确认该告警 | 1 条 UNREGISTERED_PEER，count=3，含 AE/IP 与建议处置；确认后 acknowledged，审计 ALERT_ACK | alerts=1 type=UNREGISTERED_PEER count=3 details={'called_ae': 'PACS_B', 'calling_ae': 'ROGUE', 'ip': '127.0.0.1', 'reason': "未登记的 AE 'ROGUE'"} suggestion=核实该设备是否应接入；如应接入，由信息科… status=acknowledged ack_audit=1 | 通过 | E2 |
| TC-R17-P1 | 正常演示流程零告警 | 登记、C-ECHO、2 台设备并发 C-STORE、C-FIND、C-MOVE、C-GET、相同实例重发、传输中断后重发、全量核查 | 告警表为空 | stores ct=[0, 0, 0, 0, 0, 0] es=[0, 0, 0, 0] verify_anomalies=0 alerts=[] | 通过 | E2 |
| TC-R18-N1 | 中间记录被修改 | 直接修改第 k=3 条审计记录的 details 后校验 | 校验失败，第一个断点为第 3 条，告警 AUDIT_CHAIN_BROKEN | ok=False first_break=3 problems=['第 3 条内容与 entry_hash 不符（记录被修改）'] | 通过 | E2 |
| TC-R18-N2 | 尾部被截断 | 删除数据库最后 3 条审计（锚点不动）后校验 | 校验失败，报告锚点最大 seq 大于数据库最大 seq | ok=False db_head=9 anchor_max=12 problems=['尾部截断：锚点最大 seq=12 大于数据库最大 seq=9'] | 通过 | E2 |
| TC-R18-N3 | 与带外链头不一致 | 记录带外链头后再追加 3 条；同时截断数据库与锚点的最后 5 条；分别不带 / 带带外链头校验 | 不带链头时校验通过（检测边界）；带链头时失败，报告带外链头不在当前链中 | 不带链头 ok=True；带链头 ok=False problems=['带外链头 13:6a877717789b… 不在当前链中（可能被整体截断或重算）'] | 通过 | E2 |
| TC-R18-P1 | 操作与审计一一对应且链完整 | 1 次 C-ECHO、1 次 C-STORE、1 次 C-FIND 后执行审计校验 | 依次出现 DICOM_ECHO、STORE_ACCEPTED、DICOM_FIND；校验 ok 并给出链头 | order ECHO=11 STORE=14 FIND=17 verify_ok=True head=18:f093660d440f… | 通过 | E2 |
| TC-R19-N1 | 孤儿文件被发现并隔离 | 服务停止期间在存储目录放入一个无索引的 DICOM 文件，再启动 | 告警 ORPHAN_FILE，文件移入隔离区，C-FIND 查不到 | ORPHAN_FILE=1 orphan_exists=False quarantine=1 find=0 | 通过 | E2 |
| TC-R19-P1 | 重启后数据仍在 | 5 张 CT 入库后停止服务，再用同一 runtime 启动 | C-FIND 可查到、C-GET 可取回、核查 0 异常 | find=1 instances=5 get_completed=5 verify_anomalies=0 | 通过 | E2 |
| TC-R20-N1 | 界面拒绝非法白名单输入 | 在白名单表单提交 17 个字符的 AE Title | 显示错误信息，白名单行数不变 | message='未保存：AE Title 超过 16 个字符：17' rows_before=7 rows_after=7 | 通过 | E4 |
| TC-R20-N2 | 审计页没有修改或删除入口 | 检查审计页全部按钮文字与表格编辑属性 | 不存在删除 / 编辑 / 修改类按钮，表格不可编辑 | buttons=['刷新', '校验审计链', '导出 CSV'] editTriggers=0 | 通过 | E4 |
| TC-R20-P1 | 六个页面都显示测试数据并截图 | QT_QPA_PLATFORM=offscreen，有测试数据的 runtime，依次切换六页，在影像库选中一张 CT、在核查页运行全量核查 | 白名单行数、患者树节点、告警行、审计行、核查异常行均 > 0；每页保存截图 | counts={'peers': 7, 'patients': 2, 'alerts': 2, 'audit': 30, 'findings': 1} preview=True ADMIN_VIEW 审计=2 screenshots=['01-服务状态.png', '02-白名单.png', '03-影像库.png', '04-完整性核查.png', '05-告警.png', '06-审计.png'] | 通过 | E4 |
| TC-R21-N1 | Storage Commitment 上下文被拒 | 已登记 CT_SIM_1 提议 Storage Commitment Push Model（1.2.840.10008.1.20.1）与 Verification | Storage Commitment 上下文结果 0x03（abstract syntax not supported），对端无法发 N-ACTION | established=True results={'1.2.840.10008.1.20.1': 3, '1.2.840.10008.1.1': 0} | 通过 | E2 |
| TC-R21-N2 | 不支持的传输语法被拒 | CT_SIM_1 只以 Deflated Explicit VR LE 提议 CT Image Storage | 上下文结果 0x04（transfer syntaxes not supported） | established=True results={'1.2.840.10008.5.1.4.1.1.2': 4, '1.2.840.10008.1.1': 0} | 通过 | E2 |
| TC-R21-P1 | 按一致性声明用独立实现（DCMTK 3.7.1）完成五种服务 | echoscu、storescu（3 张 CT）、findscu（STUDY 级）、movescu（目的地为 movescu 自身 SCP）、getscu；另以未登记 AE 运行 echoscu | 五项均成功（退出码 0），move/get 收到 3 个文件且像素哈希与基线一致；未登记 AE 被拒 | echoscu rc=0；storescu rc=0；findscu rc=0 pending=1；movescu rc=0 files=3 pixel_ok=True；getscu rc=0 files=3 pixel_ok=True；未登记 echoscu rc=1 rejected=True | 通过 | E3 |

## 2. 核心函数测试与文档一致性

| 编号 | 目标 | 输入 | 期望 | 实际 | 结论 | 证据 |
|---|---|---|---|---|---|---|
| TC-DOC-1 | 接口文档中的 C-STORE 状态码与代码常量一致 | store.py / netserver.py 中的状态常量；一致性声明第 4 节表格 | 代码中每个状态码都出现在文档表格中，文档表格没有代码不存在的码 | doc=['0x0', '0x124', '0xa700', '0xa900', '0xc001', '0xc002', '0xc003', '0xc004', '0xc010', '0xc011', '0xc0ff'] code=['0x0', '0x124', '0xa700', '0xa900', '0xc001', '0xc002', '0xc003', '0xc004', '0xc010', '0xc011', '0xc0ff'] | 通过 | E1 |
| TC-DOC-2 | 实现 UID 与存储类附录和代码一致 | dicomutil 常量、netserver.STORAGE_UIDS、附录 A | 一致性声明写明的实现 UID / 版本名与代码相同；附录 A 的 UID 集合等于 B 实际支持的存储类 | impl_uid_in_doc=True appendix=120 code=120 | 通过 | E1 |
| TC-DOC-3 | 文档中的传输语法顺序与代码一致 | 一致性声明 3.1 表格；netserver.STORAGE_TRANSFER_SYNTAXES | 顺序完全一致 | doc=['1.2.840.10008.1.2.1', '1.2.840.10008.1.2', '1.2.840.10008.1.2.4.70', '1.2.840.10008.1.2.4.80', '1.2.840.10008.1.2.4.90', '1.2.840.10008.1.2.5', '1.2.840.10008.1.2.4.50', '1.2.840.10008.1.2.4.91', '1.2.840.10008.1.2.2'] | 通过 | E1 |
| TC-DOC-4 | OpenSpec 场景编号与自动化测试一一对应，需求文档覆盖 R1–R21 | specs/*/spec.md 的 Scenario 编号、tests/ 的 docstring 编号、../模块三_需求定义文档.md | 每个 spec 场景都有同编号测试；每个 TC-R/CC 测试都在 spec 中；需求文档第 9、10 节包含 R1–R21 | spec=68 tests=68 spec_only=[] test_only=[] missing_in_req_doc=[] | 通过 | E1 |
| TC-U-ALR-1 | 告警聚合与时间窗口 | 同 key 连续 3 次；窗口 0 秒后再 1 次 | 第一批聚合为 count=3；超窗后新建告警 | after3=[3] after_window=[1, 3] | 通过 | E1 |
| TC-U-AUD-1 | 审计哈希链与 HMAC | 追加 5 条后校验；用错误密钥校验 | 正确密钥 ok；错误密钥报 HMAC 不符 | ok=True entries=5 wrong_key_ok=False | 通过 | E1 |
| TC-U-AUD-2 | 删除中间记录可检出 | 删除 seq=2 | 报告序号缺口 | ok=False first_break=3 | 通过 | E1 |
| TC-U-CFG-1 | 配置保存与加载一致 | 修改 port、retrieve_policy 后 save 再 load | 值一致，非法策略被拒 | port=12345 policy=allow | 通过 | E1 |
| TC-U-DB-1 | 数据库表结构创建 | 新建 Database | 包含 peers/patients/studies/series/instances/baselines/alerts/audit 等表 | tables=['alerts', 'audit', 'baselines', 'findings', 'instances', 'patients', 'peers', 'quarantine', 'series', 'studies', 'verifications'] | 通过 | E1 |
| TC-U-INT-1 | 元数据差异与分块定位 | 改一个字段；改一个分块 | 给出字段前后值；给出变化区域 | meta_diff=[{'tag': '(0008,0020)', 'keyword': 'StudyDate', 'key_field': True, 'before': '20261105', 'after': '20261106'}] tile_regions=[{'frame': 1, 'tiles_changed': 1, 'row_range': [32, 63], 'col_range': [32, 63]}] | 通过 | E1 |
| TC-U-QRY-1 | C-FIND 匹配规则 | 通配符、PN 大小写、日期范围、UID 列表、通用匹配 | 与一致性声明一致 | 8 条匹配断言全部成立 | 通过 | E1 |

## 3. 需求覆盖矩阵（每条需求至少一正一反）

| 需求 | 名称 | 正向用例 | 反向用例 | 全部通过 |
|---|---|---|---|---|
| R1 | 已登记对端连通性验证 | TC-R1-P1 | TC-R1-N1 | 是 |
| R2 | 白名单准入 | TC-R2-P1 | TC-R2-N1, TC-R2-N2, TC-R2-N3 | 是 |
| R3 | 按角色授权服务 | TC-R3-P1 | TC-R3-N1, TC-R3-N2 | 是 |
| R4 | 白名单在线维护 | TC-R4-P1 | TC-R4-N2, TC-R4-N1[AAAAAAAAAAAAAAAAA], TC-R4-N1[BAD\\AE], TC-R4-N1[], TC-R4-N1[   ], TC-R4-N1[TAB\tAE], TC-R4-N1[\u4e2d\u6587AE], TC-R4-N1b | 是 |
| R5 | 持久化后才确认成功 | TC-R5-P1, TC-R5-P2 | TC-R5-N1, TC-R5-N2 | 是 |
| R6 | 入库校验与层级一致性 | TC-R6-P1 | TC-R6-N1, TC-R6-N2, TC-R6-N3 | 是 |
| R7 | 重复实例与 UID 冲突 | TC-R7-P1, TC-R7-P2 | TC-R7-N1, TC-R7-N2 | 是 |
| R8 | 按患者/检查/序列/实例组织 | TC-R8-P1 | TC-R8-N1, TC-R8-N2, TC-R8-N3 | 是 |
| R9 | C-FIND 分层查询 | TC-R9-P1, TC-R9-P2 | TC-R9-N1, TC-R9-N2, TC-R9-N3 | 是 |
| R10 | C-MOVE 推送到已登记目的地 | TC-R10-P1 | TC-R10-N1, TC-R10-N2 | 是 |
| R11 | C-GET 同关联取回 | TC-R11-P1 | TC-R11-N1, TC-R11-N2 | 是 |
| R12 | 并发接收与并发调阅 | TC-CC1-P1 | TC-CC3-N1, TC-CC4-N1 | 是 |
| R13 | 传输中断不算事故 | TC-R13-P1 | TC-R13-N1 | 是 |
| R14 | 入库时建立受保护的基线 | TC-R14-P1 | TC-R14-N1 | 是 |
| R15 | 完整性核查与分类报告 | TC-R15-P1 | TC-R15-N1, TC-R15-N2, TC-R15-N3, TC-R15-N4 | 是 |
| R16 | 调阅前校验与默认阻断 | TC-R16-P1, TC-R16-P2 | TC-R16-N1 | 是 |
| R17 | 异常告警与正常操作零误报 | TC-R17-P1 | TC-R17-N1 | 是 |
| R18 | 哈希链审计与截断检测 | TC-R18-P1 | TC-R18-N1, TC-R18-N2, TC-R18-N3 | 是 |
| R19 | 重启持久化与启动对账 | TC-R19-P1 | TC-R19-N1 | 是 |
| R20 | 管理控制台与命令行 | TC-R20-P1 | TC-R20-N1, TC-R20-N2 | 是 |
| R21 | 按成文接口文档可对接 | TC-R21-P1 | TC-R21-N1, TC-R21-N2 | 是 |

## 4. 未执行的验证（如实列出）

- **H 真人 GUI 验收**：未执行。离屏截图（`docs/evidence/screenshots/`）只证明页面能渲染数据，不代表可用性验收。
- **X 跨组联调**：未执行。A / C 组的真实对端不存在；协议与模板见 `docs/interface/cross-group-integration-plan.md`。
- **医院真实负载**：并发阈值是实验参数（TC-CC1-P1），不是医院 SLA。
