import os
from pathlib import Path


def data_dir() -> Path:
    root = Path(os.environ.get("OGUREC_DATA_DIR", "data"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def data_file(name: str, legacy: str | None = None) -> Path:
    dest = data_dir() / name
    if legacy and not dest.exists():
        old = Path(legacy)
        if old.is_file():
            dest.write_bytes(old.read_bytes())
    return dest
