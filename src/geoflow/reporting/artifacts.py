import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_atomic(path: Path, content: bytes) -> None:
    temporary = None
    try:
        # Short names avoid exceeding Windows path limits in nested run directories.
        with NamedTemporaryFile(
            dir=path.parent, prefix=".gf-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, data: dict) -> None:
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode())
