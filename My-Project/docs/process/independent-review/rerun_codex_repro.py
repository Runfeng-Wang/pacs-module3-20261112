r"""原样执行 Codex 的复现脚本 work/repro_integrity_edges.py，只做两处替换（均用 assert 确认替换命中）：
1) 结果写到本目录的 <输出文件>，不覆盖 Codex 的 work/independent-repro/results.json；
2) 临时 runtime 放到系统临时目录，不在 Codex 的 independent-repro 目录下新建子目录。
用法：.venv\Scripts\python.exe docs\process\independent-review\rerun_codex_repro.py <输出文件名>
"""
import sys
import tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
orig = here.parents[4] / "repro_integrity_edges.py"          # work/repro_integrity_edges.py
src = orig.read_text(encoding="utf-8")
a = "(base / 'independent-repro' / 'results.json')"
b = "base / 'independent-repro' / (scenario"
assert a in src and b in src, "Codex 脚本内容与预期不符，未执行"
src = src.replace(a, "(OUT)").replace(b, "SCRATCH / (scenario")
out = here / (sys.argv[1] if len(sys.argv) > 1 else "results-rerun.json")
scratch = Path(tempfile.mkdtemp(prefix="pacs-repro-"))
print(f"source={orig} out={out} scratch={scratch}")
exec(compile(src, str(orig), "exec"), {"__file__": str(orig), "__name__": "__main__", "OUT": out,
                                       "SCRATCH": scratch})
