"""运行配置与 runtime 目录布局。

runtime/
  pacs.json           配置（本文件读写）
  data/pacs.db        SQLite 索引、基线、告警、审计
  data/store/         库内 DICOM 文件（原样数据集字节 + 服务器生成的 File Meta）
  data/quarantine/    隔离区（冲突对象、孤儿文件、损坏副本对应的新数据）
  data/tmp/           落盘临时文件
  keys/hmac.key       基线与审计 HMAC 密钥（与 data/ 分开存放）
  anchors/audit-anchor.log  审计锚点（与数据库分开存放）

所有默认值都是“实验基线假设，未经医院客户确认”。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_AE_TITLE = "PACS_B"
DEFAULT_PORT = 11112


@dataclass
class PacsConfig:
    runtime_dir: Path
    ae_title: str = DEFAULT_AE_TITLE
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    max_associations: int = 20
    # D6：调阅时异常影像的处理策略，"block"（默认）或 "allow"
    retrieve_policy: str = "block"
    # 周期扫描间隔（分钟），0 = 关闭
    periodic_scan_minutes: int = 0
    read_back_verify: bool = True
    acse_timeout: float = 30.0
    dimse_timeout: float = 60.0
    network_timeout: float = 60.0
    max_pdu: int = 16384
    # 同一来源的拒绝类告警聚合窗口（秒）
    alert_aggregate_window_s: int = 60
    # 仅测试使用：FIND/GET 每发一个 pending 后的停顿（秒），使 C-CANCEL 用例可重复；生产应为 0
    test_pending_delay_s: float = 0.0
    # 仅测试使用的故障注入开关：{"fail_write": bool, "fail_db": bool}
    fault_injection: dict = field(default_factory=dict)

    # ---- 目录 ----
    @property
    def data_dir(self) -> Path:
        return self.runtime_dir / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pacs.db"

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def key_dir(self) -> Path:
        return self.runtime_dir / "keys"

    @property
    def anchor_path(self) -> Path:
        return self.runtime_dir / "anchors" / "audit-anchor.log"

    @property
    def config_path(self) -> Path:
        return self.runtime_dir / "pacs.json"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.store_dir, self.quarantine_dir, self.tmp_dir,
                  self.key_dir, self.anchor_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    # ---- 读写 ----
    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("runtime_dir")
        d.pop("fault_injection")
        return d

    def save(self) -> None:
        self.ensure_dirs()
        self.config_path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    @classmethod
    def load(cls, runtime_dir: Path | str) -> "PacsConfig":
        runtime_dir = Path(runtime_dir).resolve()
        cfg = cls(runtime_dir=runtime_dir)
        if cfg.config_path.exists():
            data = json.loads(cfg.config_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(cfg, k) and k not in ("runtime_dir", "fault_injection"):
                    setattr(cfg, k, v)
        if cfg.retrieve_policy not in ("block", "allow"):
            raise ValueError(f"retrieve_policy 只能是 block 或 allow：{cfg.retrieve_policy}")
        return cfg
