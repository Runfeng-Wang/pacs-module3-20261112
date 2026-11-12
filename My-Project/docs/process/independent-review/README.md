# 独立复核后修复的证据（2026-11-12）

| 文件 | 内容 |
|---|---|
| `codex-repro_integrity_edges.py` | Codex 的复现脚本原样副本（原件 `work/repro_integrity_edges.py`，SHA-256 `b06f541aa2f784179793a0667d49350ceaa5c02589f1ef7eca7d4209944dd04f`） |
| `codex-results-before-fix.json` | Codex 实际运行的结果原样副本（原件 `work/independent-repro/results.json`，SHA-256 `2d7d0f0b57305d8a05875b93a58927e5b1b86b1d696d45ef4eef75f695fe3efc`；原件未被改动） |
| `rerun_codex_repro.py` | Claude 的包装脚本：原样执行 Codex 脚本，只把输出文件和临时 runtime 改到别处（两处替换都用 assert 确认命中），避免覆盖 Codex 的原始结果 |
| `claude-rerun-before-fix.json` | 修复**前**由 Claude 重跑：与 Codex 的结果一致（JSONDecodeError、0 告警；状态 49681 = 0xC211、索引 1 行、文件 0 个） |
| `claude-rerun-after-fix.json` | 修复**后**由 Claude 重跑（见下） |

修复后的结果：
- `malformed_baseline_json`：completed = true，类别为 BASELINE_TAMPERED，告警 1 条。
- `audit_anchor_write_failure`：状态 42753 = 0xA701，索引 0 行，文件 0 个。

这个复现里锚点从关联建立时就开始失败，所以走的是“降级预先拒绝”路径。“提交后锚点失败 → 补偿撤销”的路径由 TC-R5-N3 专门覆盖（只让 STORE_ACCEPTED 的锚点写入失败）。

修复设计见 `openspec/changes/add-pacs-image-server/design.md` 的 D-L、D-M；回归用例见 `tests/test_independent_review_fixes.py`：TC-R14-N2、TC-R15-N5、TC-R16-N2、TC-R5-N3、TC-R5-N4、TC-R18-N4。
