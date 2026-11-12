"""R21：按一致性声明对接（DCMTK 独立实现，证据级别 E3）；不支持的服务 / 传输语法被拒（E2）。"""

import os
import re
import subprocess
from pathlib import Path

import pytest
from pydicom import dcmread
from pydicom.uid import CTImageStorage, DeflatedExplicitVRLittleEndian
from pynetdicom import AE
from pynetdicom.sop_class import StorageCommitmentPushModel, Verification

from conftest import free_port
from pacs_server.sim import synth

ROOT = Path(__file__).resolve().parents[1]
DCMTK_BIN = Path(os.environ.get("PACS_DCMTK_BIN", ROOT.parents[1] / "tools" / "dcmtk" /
                                "dcmtk-3.7.1-win64-dynamic" / "bin"))


def _negotiate(pacs, ae_title, contexts):
    ae = AE(ae_title=ae_title)
    for c in contexts:
        ae.add_requested_context(*c)
    a = ae.associate(pacs.host, pacs.port, ae_title="PACS_B")
    results = {}
    established = a.is_established
    if established:
        for cx in a.rejected_contexts:
            results[str(cx.abstract_syntax)] = cx.result
        for cx in a.accepted_contexts:
            results[str(cx.abstract_syntax)] = 0
        a.release()
    return established, results


def test_r21_n1_storage_commitment_rejected(pacs, actual):
    """TC-R21-N1 | 目标: Storage Commitment 上下文被拒 | 输入: 已登记 CT_SIM_1 提议 Storage Commitment Push Model（1.2.840.10008.1.20.1）与 Verification | 期望: Storage Commitment 上下文结果 0x03（abstract syntax not supported），对端无法发 N-ACTION"""
    est, res = _negotiate(pacs, "CT_SIM_1", [(StorageCommitmentPushModel,), (Verification,)])
    actual(f"established={est} results={res}")
    assert est and res[str(StorageCommitmentPushModel)] == 0x03 and res[str(Verification)] == 0


def test_r21_n2_unsupported_transfer_syntax_rejected(pacs, actual):
    """TC-R21-N2 | 目标: 不支持的传输语法被拒 | 输入: CT_SIM_1 只以 Deflated Explicit VR LE 提议 CT Image Storage | 期望: 上下文结果 0x04（transfer syntaxes not supported）"""
    est, res = _negotiate(pacs, "CT_SIM_1", [(CTImageStorage, [DeflatedExplicitVRLittleEndian]),
                                              (Verification,)])
    actual(f"established={est} results={res}")
    assert est and res[str(CTImageStorage)] == 0x04


def _run(exe, *args, timeout=120):
    p = subprocess.run([str(DCMTK_BIN / exe), *map(str, args)], capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


@pytest.mark.dcmtk
@pytest.mark.skipif(not (DCMTK_BIN / "echoscu.exe").exists(), reason="DCMTK 不在 work/tools/dcmtk")
def test_r21_p1_dcmtk_interop(pacs, tmp_path, actual):
    """TC-R21-P1 | 目标: 按一致性声明用独立实现（DCMTK 3.7.1）完成五种服务 | 输入: echoscu、storescu（3 张 CT）、findscu（STUDY 级）、movescu（目的地为 movescu 自身 SCP）、getscu；另以未登记 AE 运行 echoscu | 期望: 五项均成功（退出码 0），move/get 收到 3 个文件且像素哈希与基线一致；未登记 AE 被拒"""
    host, port = pacs.host, str(pacs.port)
    dport = free_port()
    pacs.svc.whitelist.add("DCMTK_WS", "127.0.0.1", ["STORE", "QUERY", "RETRIEVE", "MOVE_DEST"],
                           port=dport, operator="pytest")
    st = synth.SynthStudy(synth.DEFAULT_PATIENTS[0], "20261105")
    paths = synth.write_datasets(synth.ct_series(st, 3, 64, 64), tmp_path / "ct")
    log = []
    rc_e, out_e = _run("echoscu.exe", "-v", "-aet", "DCMTK_WS", "-aec", "PACS_B", host, port)
    log.append(f"echoscu rc={rc_e}")
    rc_s, out_s = _run("storescu.exe", "-v", "-aet", "DCMTK_WS", "-aec", "PACS_B", host, port, *paths)
    log.append(f"storescu rc={rc_s}")
    rc_f, out_f = _run("findscu.exe", "-v", "-S", "-aet", "DCMTK_WS", "-aec", "PACS_B",
                       "-k", "QueryRetrieveLevel=STUDY", "-k", "PatientID=SYN0001",
                       "-k", "StudyInstanceUID", "-k", "NumberOfStudyRelatedInstances", host, port)
    n_find = len(re.findall(r"Find Response: \d+ \(Pending\)", out_f))
    log.append(f"findscu rc={rc_f} pending={n_find}")
    mdir, gdir = tmp_path / "move", tmp_path / "get"
    mdir.mkdir(); gdir.mkdir()
    rc_m, out_m = _run("movescu.exe", "-v", "-S", "-aet", "DCMTK_WS", "-aem", "DCMTK_WS",
                       "-aec", "PACS_B", "--port", dport, "-od", mdir,
                       "-k", "QueryRetrieveLevel=STUDY", "-k", f"StudyInstanceUID={st.study_uid}",
                       host, port)
    rc_g, out_g = _run("getscu.exe", "-v", "-S", "-aet", "DCMTK_WS", "-aec", "PACS_B", "-od", gdir,
                       "-k", "QueryRetrieveLevel=STUDY", "-k", f"StudyInstanceUID={st.study_uid}",
                       host, port)
    rc_r, out_r = _run("echoscu.exe", "-v", "-aet", "NOT_LISTED", "-aec", "PACS_B", host, port)

    def px_ok(d):
        files = [p for p in d.iterdir() if p.is_file()]
        ok = all(__import__("hashlib").sha256(bytes(dcmread(p).PixelData)).hexdigest() ==
                 pacs.svc.verifier.instance_row(str(dcmread(p).SOPInstanceUID))["pixel_sha256"]
                 for p in files)
        return len(files), ok

    nm, okm = px_ok(mdir)
    ng, okg = px_ok(gdir)
    log += [f"movescu rc={rc_m} files={nm} pixel_ok={okm}", f"getscu rc={rc_g} files={ng} pixel_ok={okg}",
            f"未登记 echoscu rc={rc_r} rejected={'Rejected' in out_r or 'rejected' in out_r}"]
    (ROOT / "docs" / "testing" / "logs").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "testing" / "logs" / "dcmtk-interop.log").write_text(
        "\n\n".join([f"== echoscu ==\n{out_e}", f"== storescu ==\n{out_s}", f"== findscu ==\n{out_f}",
                     f"== movescu ==\n{out_m}", f"== getscu ==\n{out_g}",
                     f"== echoscu (未登记 AE) ==\n{out_r}"]), encoding="utf-8")
    actual("；".join(log))
    assert rc_e == 0 and rc_s == 0 and rc_f == 0 and n_find == 1
    assert rc_m == 0 and nm == 3 and okm
    assert rc_g == 0 and ng == 3 and okg
    assert rc_r != 0 and ("Rejected" in out_r or "rejected" in out_r)
    assert pacs.svc.db.scalar("SELECT COUNT(*) FROM instances") == 3
