import fcntl
from contextlib import contextmanager
from pathlib import Path


class Busy(Exception):
    pass


@contextmanager
def write_lock(root: Path):
    (root / "data").mkdir(parents=True, exist_ok=True)
    with (root / "data" / "run.lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Busy("another writer is busy (data/run.lock)") from error
        yield
