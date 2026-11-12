# 独立复核前的测试记录（保留）

2026-11-12 第一次完整运行：85 个用例全部通过（pytest-full.log / junit.xml / test-report.md 原样保留）。
此后 Codex 独立复核发现 2 个异常处理问题：这次运行的用例中没有覆盖这两类故障注入，因此“全部通过”并不说明这两种情形是正确的。修复与新增回归用例见 `../../../process/independent-review/` 和 `tests/test_independent_review_fixes.py`。
