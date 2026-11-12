# Proposal

## Why

课程模块三项目 B 的客户（医院信息科）提出：全院影像设备把图像传到服务器集中存储；按患者、检查组织；支持随时查询与调阅；影像科工作站通过 DICOM 标准协议调阅；多台设备同时传输、多个工作站同时调阅时要能并发处理；只有登记在册的设备才能连接，白名单可以维护更新；库内图像必须与上传时保持一致，发生变化要能发现并告警；所有操作留存记录。

当前项目为空。本 change 建一个**实验性、验证性**的 PACS 原型，用来检验需求定义（`../模块三_需求定义文档.md`）是否清楚、可验收。原型可以推倒重来。所有默认假设都是“实验基线假设，未经医院客户确认”。

## What Changes

- 新增 DICOM 上层服务（B 作为 SCP）：Verification（C-ECHO）、Storage（C-STORE）、Patient Root / Study Root Query/Retrieve 的 FIND / MOVE / GET。使用真实的 DIMSE over TCP，不用伪协议，不靠文件拷贝。
- 新增白名单准入：Calling AE Title + 源 IP + 角色（STORE / QUERY / RETRIEVE / MOVE_DEST）；在关联协商阶段用真实的 A-ASSOCIATE-RJ 拒绝；白名单可在线维护，对新关联立即生效。
- 新增接收入库：原样保存网络数据集字节；只有 fsync 落盘、事务提交、建立基线之后才返回 0x0000；处理落盘失败和事务失败；处理重复 / 冲突 UID 与层级归属冲突；支持启动对账。
- 新增完整性基线与核查：文件、数据集、像素三重哈希，加元数据快照和像素分块哈希，用 HMAC 保护；支持手动核查、调阅前核查、可选的周期扫描；异常按类别输出，给出对象、字段或位置、前后值；调阅时默认阻断异常影像。
- 新增告警与审计：持久化告警（可聚合、可确认）；带 HMAC 的哈希链审计，外加本机锚点文件和带外链头核对。
- 新增管理控制台：PyQt5 图形界面与 CLI（服务启停、白名单、影像库浏览、核查、告警、审计）。
- 新增成文 DICOM 接口文档（一致性声明）、合成 DICOM 生成器、本机回环模拟对端（设备 / 工作站）、跨组联调模板（标注为未执行）。

## Capabilities

### New Capabilities

- `dicom-access-control`：AE 配置、白名单准入（AE + IP + 角色）、按角色的表示上下文授权、白名单维护。
- `image-ingest`：C-STORE 接收、入库校验、落盘与成功语义、重复与冲突、按患者 / 检查 / 序列 / 实例组织、断连处理、重启持久化与对账。
- `query-retrieve`：C-FIND 分层查询、C-MOVE / C-GET 取回、子操作计数与失败列表、取消。
- `integrity-verification`：基线建立、完整性核查与分类、调阅前校验与阻断策略。
- `alert-audit`：告警生成、聚合、确认；哈希链审计、锚点与校验、导出。
- `admin-console`：图形管理界面与命令行管理入口。
- `dicom-conformance`：对外公布的互联约定（支持与不支持的服务、状态码含义），以及不支持服务时的协议行为。

### Modified Capabilities

（无。项目此前没有任何 spec。）

## Impact

- 新代码：`src/pacs_server/`（服务核心、DICOM 网络层、存储、完整性、告警审计、CLI、GUI、模拟器）、`tests/`、`scripts/`。
- 依赖（安装在项目 `.venv`，`uv.lock` 锁定版本）：pydicom 3.0.x、pynetdicom 3.0.4、numpy、PyQt5 5.15（Qt 5.15.2），开发依赖 pytest。
- 运行数据：`runtime/`（数据库、影像文件、密钥、锚点），不纳入交付包。
- 外部系统：仅本机回环（127.0.0.1）上的模拟对端；不连接真实医院系统，不使用真实患者数据。跨组 A / C 联调本期未执行，只提供协议文档与模板。
- 不包含（本期不做）：TLS、GUI 登录 / RBAC、Storage Commitment、MWL / MPPS / WADO / DICOMweb / HL7、转码存储、删除 / 更正 / 患者合并、高可用 / 备份、诊断报告。
