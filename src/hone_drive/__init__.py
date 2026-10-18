"""hone-drive: a storage dashboard for AI developers (design/current.md, accepted in change 0001)."""

from hone_drive.advice import advise
from hone_drive.benchmark import Options as BenchOptions
from hone_drive.benchmark import bench
from hone_drive.compare import Change, compare
from hone_drive.errors import BenchmarkBusy, HoneDriveError, NotFound, UnsafeBenchmark, UnsupportedFormat
from hone_drive.estimates import Estimate, copy_time, load_time
from hone_drive.model import (
    Accounting,
    Advice,
    Drive,
    Duplicate,
    Item,
    Link,
    MachineFacts,
    Measurement,
    Run,
    Store,
    Traits,
    Volume,
)
from hone_drive.ports import Probe
from hone_drive.report import report
from hone_drive.runs import list_runs, load_run, save_run
from hone_drive.scanner import probe_for, scan

__all__ = [
    "Accounting",
    "Advice",
    "BenchOptions",
    "BenchmarkBusy",
    "Change",
    "Drive",
    "Duplicate",
    "Estimate",
    "HoneDriveError",
    "Item",
    "Link",
    "MachineFacts",
    "Measurement",
    "NotFound",
    "Probe",
    "Run",
    "Store",
    "Traits",
    "UnsafeBenchmark",
    "UnsupportedFormat",
    "Volume",
    "__version__",
    "advise",
    "bench",
    "compare",
    "copy_time",
    "list_runs",
    "load_run",
    "load_time",
    "probe_for",
    "report",
    "save_run",
    "scan",
]

__version__ = "0.0.1"
