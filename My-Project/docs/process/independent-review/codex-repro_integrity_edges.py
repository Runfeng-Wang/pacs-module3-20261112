from pathlib import Path
import json
import sys
import uuid

base = Path(__file__).resolve().parent
project = base / 'submission' / 'My-Project'
sys.path.insert(0, str(project / 'tests'))
sys.path.insert(0, str(project / 'src'))
from conftest import make_pacs, store_all
from pacs_server.sim import synth

results = []
for scenario in ('malformed_baseline_json', 'audit_anchor_write_failure'):
    scratch = base / 'independent-repro' / (scenario + '-' + uuid.uuid4().hex[:8])
    scratch.mkdir(parents=True)
    p = make_pacs(scratch)
    try:
        st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0], '20261105')
        datasets = synth.ct_series(st, 1, 32, 32)
        paths = synth.write_datasets(datasets, scratch / 'input')
        if scenario == 'malformed_baseline_json':
            assert store_all(p, 'CT_SIM_1', paths) == [0]
            with p.svc.db.write() as conn:
                conn.execute("UPDATE baselines SET meta_json='{' ")
            try:
                report = p.svc.verifier.run(operator='independent-review')
                result = {'completed': True, 'categories': [x['category'] for x in report['findings']]}
            except Exception as exc:
                result = {'completed': False, 'exception': type(exc).__name__, 'message': str(exc)}
            result['baseline_alerts'] = p.svc.alerts.count(None, 'BASELINE_TAMPERED')
        else:
            original_anchor = p.svc.audit.anchor
            def fail_anchor(record):
                raise OSError('independent fault injection: audit anchor write unavailable')
            p.svc.audit.anchor = fail_anchor
            try:
                statuses = store_all(p, 'CT_SIM_1', paths)
                result = {'store_statuses': statuses}
            except Exception as exc:
                result = {'exception': type(exc).__name__, 'message': str(exc)}
            finally:
                p.svc.audit.anchor = original_anchor
            result.update({'index_rows': p.svc.db.scalar('SELECT COUNT(*) FROM instances'),
                           'stored_files': len(list(p.svc.config.store_dir.rglob('*.dcm')))})
        results.append({'scenario': scenario, **result})
    finally:
        p.svc.close()
(base / 'independent-repro' / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(results, indent=2, ensure_ascii=False))
