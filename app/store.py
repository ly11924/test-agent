"""本地文件存储：一次实验一个 JSON，逐步覆盖写入（原子替换）。"""

import json
from pathlib import Path


class RunStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.data_dir / f"{run_id}.json"

    def save(self, run: dict) -> Path:
        path = self._path(run["run_id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # 原子替换，避免读到半截文件
        return path

    def load(self, run_id: str) -> dict | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
