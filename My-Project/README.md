# PACS B 实验原型（模块三 · 项目 B：PACS 图像服务器）

本项目是**实验性、验证性**的软件，用来检验需求定义（`../模块三_需求定义文档.md`）做得好不好，之后可以推倒重来或迭代。

- 使用真实的 DICOM 网络协议（pynetdicom 实现 C-ECHO / C-STORE / C-FIND / C-MOVE / C-GET SCP），不是伪协议。
- **只用合成 DICOM 和本机回环（127.0.0.1）对端**，不连接真实医院系统，不含真实患者数据。
- 跨组（A 采集 / C 阅片）真实联调**尚未执行**，见 `docs/interface/cross-group-integration-plan.md`。
- 所有默认参数都是“实验基线假设，未经医院客户确认”。

## 1. 功能一览

| 能力 | 说明 | 需求 |
|---|---|---|
| 白名单准入 | Calling AE + 源 IP + 角色（STORE / QUERY / RETRIEVE / MOVE_DEST）；在关联层用真实 A-ASSOCIATE-RJ 拒绝；在线维护、立即生效 | R1–R4 |
| 接收入库 | 原样保存网络数据集字节；fsync + 读回比对 + 原子重命名 + 事务提交之后才返回 0x0000；落盘或事务失败不留残留；重复 / 等价 / 冲突 / 已存副本损坏分别处理；层级冲突拒绝 | R5–R8、R13、R19 |
| 查询调阅 | Patient Root / Study Root 的 C-FIND（通配符、日期范围、UID 列表、中文 ISO_IR 192）、C-MOVE（目的地只取自白名单）、C-GET、C-CANCEL | R9–R11 |
| 并发 | 每个关联一个线程；按 UID 加锁；本地实验阈值：250 个实例并发入库 + 2 个工作站并发取回 | R12 |
| 完整性 | 文件 / 数据集 / 像素三重 SHA-256 + 元数据快照 + 像素分块哈希，HMAC 保护；核查结果分类并给出字段前后值和像素块位置；调阅前校验，默认阻断异常影像 | R14–R16 |
| 告警与审计 | 本地持久化告警（可聚合、可确认）；带 HMAC 的哈希链审计 + 锚点文件 + 带外链头核对；导出 CSV | R17–R18 |
| 管理入口 | PyQt5 控制台（六个页面）+ `pacs` 命令行 | R20 |
| 接口文档 | DICOM 一致性声明（对端照文档即可对接），并已用 DCMTK 3.7.1 在本机验证 | R21 |

## 2. 环境与安装（Windows）

需要 [uv](https://docs.astral.sh/uv/) 和 Python 3.12。在 `My-Project` 目录下用 PowerShell 执行：

```powershell
uv sync                         # 按 uv.lock 把依赖装进项目自己的 .venv（不改全局环境）
# 如果 uv 无法自动获取 Python，可指定一个已有的 3.12 解释器：
# uv sync --python "C:\path\to\python.exe"
.venv\Scripts\pacs.exe --help
```

锁定版本：pydicom 3.0.2、pynetdicom 3.0.4、numpy 2.5.4、PyQt5 5.15.11（PyQt5-Qt5 5.15.2）、pytest 9.2.0。

## 3. 快速演示

```powershell
# 1) 端到端演示：新建 runtime\demo → 登记白名单 → 并发入库 → 查询 / 调阅 → 未登记设备被拒
#    → 篡改注入 → 核查 → 调阅阻断 → 审计校验（共 13 步，自检 10 项）
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1

# 2) 打开管理控制台查看演示结果（白名单 / 影像库 / 核查 / 告警 / 审计）
powershell -ExecutionPolicy Bypass -File scripts\start-gui.ps1 -Runtime runtime\demo
```

一次实际运行的输出见 `docs/testing/logs/demo-run.log`，控制台截图见 `docs/evidence/screenshots/`。

## 4. 作为服务运行，并用模拟设备 / 工作站操作

```powershell
# 终端 1：启动 SCP（默认 PACS_B @ 127.0.0.1:11112）
powershell -ExecutionPolicy Bypass -File scripts\start-server.ps1 -Runtime runtime\main

# 终端 2：维护白名单（对新关联立即生效，无需重启）
$p = ".venv\Scripts\pacs.exe"
& $p --runtime runtime\main peer add --ae CT_SIM_1 --ip 127.0.0.1 --roles STORE
& $p --runtime runtime\main peer add --ae WS_SIM_1 --ip 127.0.0.1 --port 11113 --roles QUERY RETRIEVE MOVE_DEST
& $p --runtime runtime\main synth runtime\input --ct 10 --endo 3      # 生成合成 DICOM
& $p sim store --ae CT_SIM_1 runtime\input\ct_syn0001                 # 模拟 CT 设备 C-STORE
& $p sim find --ae WS_SIM_1 --level STUDY --key PatientID=SYN0001 --key StudyInstanceUID= --key NumberOfStudyRelatedInstances=
& $p sim move --ae WS_SIM_1 --listen 11113 --key StudyInstanceUID=<上一步的UID>
& $p sim echo --ae ROGUE                                               # 未登记 → 被拒、产生告警
& $p --runtime runtime\main verify                                     # 完整性核查（有异常时退出码 1）
& $p --runtime runtime\main alerts
& $p --runtime runtime\main audit verify
& $p --runtime runtime\main audit head                                 # 带外记录链头
```

独立实现对接（DCMTK，便携版位于 `work/tools/dcmtk`，不随项目交付）：

```powershell
echoscu -aet WS_SIM_1 -aec PACS_B 127.0.0.1 11112
storescu -aet CT_SIM_1 -aec PACS_B 127.0.0.1 11112 file.dcm
```

完整参数见 `docs/interface/dicom-conformance-statement.md`。

## 5. 测试

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-tests.ps1
```

- 运行全部 pytest（单元测试、真实 TCP 协议测试、并发、故障注入、DCMTK 互操作、PyQt5 离屏冒烟、文档一致性），
- 保存 `docs/testing/logs/pytest-full.log` 与 `junit.xml`，
- 生成 `docs/testing/test-report.md`（编号 / 目标 / 输入 / 期望 / 实际 / 结论 + 需求覆盖矩阵）。

单独运行某一类：`.venv\Scripts\python.exe -m pytest -m concurrency`、`-m gui`、`-m dcmtk`。
DCMTK 不在 `..\..\tools\dcmtk\dcmtk-3.7.1-win64-dynamic\bin` 时，相应用例会被跳过；也可以用环境变量 `PACS_DCMTK_BIN` 指定位置。

## 6. 目录结构

```
openspec/                      OpenSpec 规格：changes/add-pacs-image-server/{proposal,design,tasks}.md + specs/（7 个 capability）
src/pacs_server/
  netserver.py                 DICOM 网络层：PacsACSE 准入、按角色过滤上下文、ECHO/STORE/FIND/MOVE/GET 处理器
  store.py                     入库流水线（成功语义、重复 / 冲突、隔离、启动对账）
  integrity.py / dicomutil.py  基线、核查分类、元数据快照、像素分块哈希、File Meta 生成
  query.py                     C-FIND 匹配与取回解析
  whitelist.py audit.py alerts.py db.py config.py service.py
  cli.py demo.py gui/app.py    命令行、演示、PyQt5 控制台
  sim/                         合成数据（synth）、模拟设备 / 工作站（scu）、篡改注入（tamper）
tests/                         pytest（每个用例 docstring：编号 | 目标 | 输入 | 期望）
scripts/                       run-tests.ps1、demo.ps1、start-gui.ps1、start-server.ps1、make_test_report.py
docs/
  requirements-discussion/     Codex ↔ Claude 的多轮需求讨论记录（AI 讨论，不是人工访谈）
  interface/                   一致性声明、存储类附录、跨组联调计划（未执行）
  testing/                     测试报告与原始日志
  evidence/screenshots/        控制台离屏截图
  process/                     工作流程记录、OpenSpec CLI 日志
  known-issues.md              已知缺陷与风险
  delivery-note.md             交付说明（哪些文件不需要打包）
  templates/                   个人贡献表、项目小结（占位，需用户填写）
```

需求、规格与测试的追溯关系：需求定义文档中的 R1–R21 对应 `openspec/.../specs/*/spec.md` 里同编号的 Requirement，每个 Scenario 的 TC 编号就是 `tests/` 中 docstring 的编号，`docs/testing/test-report.md` 第 3 节给出覆盖矩阵。
