# 交付说明

- `My-Project/` 是**完整的源项目**：源码、测试、脚本、OpenSpec 规格（`openspec/`）、文档、测试日志与截图，以及锁定依赖的 `pyproject.toml` 与 `uv.lock`。
- **不需要打包**（可再生成、或属于本机运行状态）：
  - `.venv/`：运行 `uv sync` 即可重建
  - `runtime/`：演示与运行时生成的数据库、影像、密钥、锚点、导出文件，全部是合成数据
  - `__pycache__/`、`.pytest_cache/`
  - pytest 临时目录
- `.claude/` 是 `openspec init --tools claude` 生成的项目级 OpenSpec skill 与命令，属于 OpenSpec 工作流的一部分，可以保留。
- DCMTK 便携版放在 `work/tools/dcmtk`（项目之外），只用于本机互操作测试，**不随项目交付**。来源：
  `https://dicom.offis.de/download/dcmtk/current/bin/dcmtk-3.7.1-win64-dynamic.zip`，
  SHA-256 `a94ebcf1bff8bf62b29da867df8a9c903f8ec2fb7284aebb976b68c1369a192c`。
- OpenSpec change `add-pacs-image-server` **未归档**（按用户要求）。
- 最终 ZIP 由 Codex 独立核验后打包；Claude 没有生成 ZIP。
- 学员号、姓名、组员、个人贡献没有填写，需要用户本人补全（见 `templates/`）。
