# Tasks

## 1. 工程骨架与依赖

- [x] 1.1 建立 `pyproject.toml`、`uv.lock`、`.python-version` 和 `src/pacs_server/` 包骨架，并确认 `uv sync` 成功、`.venv` 能 import pydicom 3.0.x / pynetdicom 3.0.4 / PyQt5
- [x] 1.2 实现 `config.py`（PacsConfig 加载 / 保存、runtime 目录布局）与 `db.py`（SQLite WAL、写锁、表结构），并确认 `tests/test_unit_core.py::test_config_roundtrip` 和 schema 创建测试通过

## 2. 审计与告警

- [x] 2.1 实现 `audit.py`：哈希链 + HMAC + 锚点文件、`verify(expect_head)`、`head()`、导出；`tests/test_alert_audit.py` 覆盖 TC-R18-N1/N2/N3、`tests/test_unit_core.py` 覆盖链与缺口单元用例，均通过
- [x] 2.2 实现 `alerts.py`：持久化、按 aggregate_key 聚合、确认并写审计；单元测试覆盖聚合计数与 ALERT_ACK

## 3. 白名单准入与 C-ECHO

- [x] 3.1 实现 `whitelist.py`：输入校验、增删改启停、变更前后值审计、`admit()` 判定；`tests/test_unit_core.py` 覆盖 TC-R4-N1 / N1b
- [x] 3.2 实现 `netserver.py`：PacsACSE 准入（RJ 1,1,3 / 1,1,7）、按角色过滤上下文、C-ECHO、关联事件审计；`tests/test_net_access.py` 中 TC-R1-*、TC-R2-*、TC-R3-*、TC-R4-P1/N2 用真实 TCP 通过

## 4. 接收入库

- [x] 4.1 实现 `dicomutil.py`（File Meta 生成、数据集偏移、元数据快照、像素分块哈希）与 `integrity.compute_baseline`；单元测试确认哈希可复算
- [x] 4.2 实现 `store.py` 入库流水线（校验、原样落盘、fsync、读回比对、事务、故障注入钩子）；`tests/test_net_store.py` 中 TC-R5-P1/P2/N1/N2、TC-R6-* 通过
- [x] 4.3 实现重复 / 等价 / 冲突 / 已存副本损坏的判定与隔离区，以及按 UID 加锁；TC-R7-P1/P2/N1/N2 通过
- [x] 4.4 实现患者 / 检查 / 序列组织与人口学不一致标记，以及启动对账（孤儿文件）；TC-R8-*、TC-R19-P1/N1 通过
- [x] 4.5 实现可重复的传输中断注入并验证断连处理；TC-R13-P1/N1 通过

## 5. 查询与取回

- [x] 5.1 实现 `query.py`：C-FIND 分层匹配（单值、通配符、日期范围、UID 列表、中文字符集）、计数属性、上层唯一键校验、取消；`tests/test_net_query_retrieve.py` 中 TC-R9-* 通过
- [x] 5.2 实现 C-MOVE（目的地只从白名单取、0xA801 无外连、计数、失败列表、取消）；TC-R10-* 通过
- [x] 5.3 实现 C-GET（同关联子操作、角色协商失败计数、取消）；TC-R11-* 通过

## 6. 完整性核查与调阅阻断

- [x] 6.1 实现 `integrity.Verifier`：核查分类（缺失、不可解析、像素 / 分块位置、元数据前后值、结构、索引、基线 HMAC），以及核查运行记录与告警；`tests/test_integrity.py` 中 TC-R14-*、TC-R15-* 通过
- [x] 6.2 实现调阅前校验与 block / allow 策略、无损传输语法转换；TC-R16-P1/P2/N1 通过
- [x] 6.3 实现篡改注入工具 `sim/tamper.py`（像素、元数据、删除、索引、基线、审计），并在测试与演示中使用

## 7. 管理入口

- [x] 7.1 实现 `cli.py`（init、serve、peer、verify、alerts、audit、synth、sim、tamper、demo），并确认 `pacs --help` 和各子命令的 `--help` 可运行
- [x] 7.2 实现 PyQt5 控制台 `gui/app.py`（六个页面、内嵌服务启停、白名单表单校验、审计只读）；`tests/test_gui_smoke.py`（offscreen）中 TC-R20-* 通过并输出截图到 `docs/evidence/screenshots/`
- [x] 7.3 实现合成数据生成器 `sim/synth.py`（CT / 内镜 / SC，含中文姓名 ISO_IR 192），并在测试 fixture 中使用

## 8. 并发、告警零误报与互操作

- [x] 8.1 实现并发验收测试 `tests/test_concurrency.py`：TC-CC1-P1（含实测耗时记录）、TC-CC3-N1、TC-CC4-N1 通过
- [x] 8.2 编写 TC-R17-P1/N1（正常流程零告警、聚合与确认）和 TC-R18-P1，并确认通过
- [x] 8.3 编写一致性测试：TC-R21-N1（Storage Commitment 被拒）、TC-R21-N2（不支持的传输语法被拒）通过；TC-R21-P1 使用 DCMTK（如已下载到 `work/tools/dcmtk`），否则使用 pynetdicom 自带应用，并在报告中注明证据级别

## 9. 文档与集成验证

- [x] 9.1 编写 `docs/interface/dicom-conformance-statement.md`（对端照文档即可对接）和 `docs/interface/cross-group-integration-plan.md`（标注为未执行的模板），并核对文档中的状态码、实现 UID、存储类、传输语法顺序与代码常量一致，spec 场景与测试编号一一对应（`tests/test_docs_consistency.py` TC-DOC-1..4）
- [x] 9.2 编写 README（Windows 启动、演示、测试命令）、`docs/known-issues.md`、`docs/process/workflow-record.md`、交付说明，并按 README 实际跑一遍演示脚本 `scripts/demo.ps1`
- [x] 9.3 运行全部测试并用 `scripts/make_test_report.py` 生成 `docs/testing/test-report.md`（编号 / 目标 / 输入 / 期望 / 实际 / 结论），原始日志保存在 `docs/testing/logs/`
- [x] 9.4 运行 `openspec validate add-pacs-image-server --strict` 并保存日志；核对本清单的勾选与实现、测试结果一致
- [x] 9.5 完成 `../模块三_需求定义文档.md` 定稿，并与 specs、测试编号交叉核对

## 10. 独立复核后修复（Codex 复现的两个问题，本 change 内修复）

- [x] 10.1 基线不可信时核查不中断：`integrity.py` 防御式解析 meta_json / tiles_json、处理基线缺失、单实例隔离（VERIFY_ERROR）；`tests/test_independent_review_fixes.py` 中 TC-R14-N2、TC-R15-N5、TC-R16-N2 通过
- [x] 10.2 锚点写入失败的提交语义：`audit.py` 的 anchor_safely / 降级 / recover_anchors，`store.py` 的预检、提交后补偿撤销、失败路径尽力审计，`netserver.py` 的入库安全网；TC-R5-N3、TC-R5-N4、TC-R18-N4 通过
- [x] 10.3 用 `docs/process/independent-review/rerun_codex_repro.py` 原样重跑 Codex 复现脚本，保存修复前 / 修复后结果，并确认两个场景均已修复
- [x] 10.4 更新 design（D-L、D-M）、specs（R5 / R14 / R15 / R16 / R17 / R18）、一致性声明（0xA701 与成功语义）、需求定义文档、已知问题与过程记录；全套测试与 `openspec validate --strict` 通过

## Workflow follow-up

- 本 change 按用户要求**不归档**；归档留到课程评审之后由用户决定。
- 真人 GUI 验收（证据级别 H）与跨组联调（X）需用户和真实对端完成，不在本清单的勾选范围内。
