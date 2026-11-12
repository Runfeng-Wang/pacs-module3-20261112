# 跨组联调计划与记录模板（A 采集 → B 存储分发 → C 调阅）

> **状态：未执行。** 截至 2026-11-12，没有真实的 A 组或 C 组对端，也没有组员信息。本文件是计划和空白记录模板，
> 下面所有“实际结果”栏都**故意留空**，不得在未真实联调前填写。本机回环模拟验证见 `../testing/test-report.md`（证据级别 E2/E3，不是 X）。

## 1. 互联点总览

| 互联点 | 与谁连 | 传什么 | B 的角色 | 怎么确认成功 | 怎么确认失败 |
|---|---|---|---|---|---|
| I-1 A→B 存储 | A 组采集工作站（内镜） | VL Endoscopic Image 或 Secondary Capture 实例（Q-A1 待确认） | Storage SCP | 每张 C-STORE 响应 0x0000（= B 已持久化并建立基线，**不是** Storage Commitment） | 非 0x0000：按一致性声明第 4 节的处置建议处理；RJ(1,1,3) 表示未登记 |
| I-2 A→B 连通 | A 组 | C-ECHO | Verification SCP | 0x0000 | RJ / 超时 |
| I-3 C→B 查询 | C 组阅片工作站 | C-FIND 标识符 | Q/R FIND SCP | 收到期望的匹配集合，最终 0x0000 | 0xA900（缺上层唯一键）/ 0xC000 / RJ |
| I-4 C→B 调阅（MOVE） | C 组，B 再连 C 的存储 SCP | C-MOVE + C-STORE 子操作（CT Image Storage） | Q/R MOVE SCP + Storage SCU | 最终 0x0000，且完成数 = C-FIND 的 NumberOf*RelatedInstances、失败数为 0 | 0xB000 / 0xA702（看 Failed SOP Instance UID List）；0xA801（目的地未登记或连不上） |
| I-5 C→B 调阅（GET） | C 组 | C-GET + 同关联 C-STORE | Q/R GET SCP | 同 I-4 | 同 I-4（没有 0xA801） |

## 2. 联调前准备清单

- [ ] 双方交换：AE Title、IP、端口、角色、使用的 SOP 类与传输语法（按一致性声明第 2 节）
- [ ] B 管理员登记对端白名单（`pacs peer add ...`），并把截图或审计 PEER_ADDED 记录附在记录里
- [ ] B 以局域网地址启动（`pacs init --host <B的IP>` 后 `pacs serve`），用户自行确认 Windows 防火墙已放行 11112 端口（本原型不改系统设置）
- [ ] 约定测试数据：**只用合成数据**，不得使用真实患者数据
- [ ] 约定异常用例：未登记 AE、被叫 AE 错误、C-MOVE 到未登记目的地、篡改后调阅（B 默认阻断）

## 3. 联调用例与记录模板

| 编号 | 对端 | 用例 | 期望 | 实际（联调时填写） | 结论 | 证据（截图 / 日志 / 审计 seq） |
|---|---|---|---|---|---|---|
| X-1 | A 组 | C-ECHO | 0x0000 | | | |
| X-2 | A 组 | C-STORE 5 张内镜图像 | 每张 0x0000；B 中可查到 | | | |
| X-3 | A 组 | 未登记 AE 发起连接 | RJ(1,1,3)；B 告警 UNREGISTERED_PEER | | | |
| X-4 | A 组 | 同 UID 改内容后重发 | 0xC010；B 隔离并告警 | | | |
| X-5 | C 组 | C-FIND STUDY 级（PatientID） | 返回检查列表、0x0000 | | | |
| X-6 | C 组 | C-MOVE 到 C 组存储 SCP | 0x0000，完成数 = 期望张数 | | | |
| X-7 | C 组 | C-GET（若 C 组使用） | 0x0000，完成数 = 期望张数 | | | |
| X-8 | C 组 | C-MOVE 到未登记目的地 | 0xA801；B 告警；B 无外连 | | | |
| X-9 | A→B→C | A 发送的图像由 C 调阅 | C 收到的像素与 A 发送的一致（比对像素哈希） | | | |
| X-10 | C 组 | 篡改一张后调阅 | 该张被阻断（0xB000，失败列表含该 UID），B 告警 | | | |

联调记录头（每次填写）：日期时间、地点 / 网络、B 版本（`pacs status`）、对端软件与版本、双方 AE / IP / 端口、参与人（**真实姓名由用户填写**）。

## 4. 待与对端确认的问题（来自需求定义文档）

- Q-A1：A 组用 VL Endoscopic 还是 SC？传输语法是 JPEG Baseline 还是未压缩？（B 的压缩语法目前只有 E0 证据）
- Q-A2：A 组除 C-STORE 状态外，是否还需要 Storage Commitment？（B 本期不支持）
- Q-C1：C 组用 C-MOVE 还是 C-GET？存储 SCP 端口是多少？
- Q-C2：C 组接受哪些传输语法？
- Q-C3：C 组的“提示是否被改动”希望 B 提供什么（B 当前做法是阻断并告警，不在报文中夹带私有标记）？
