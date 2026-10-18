from __future__ import annotations

from hone_drive.estimates import DISK_ONLY, copy_time, load_time, read_speed, warm
from hone_drive.model import Item, Measurement, Store
from hone_drive.scanner import scan
from hone_drive.testing import reference_probe

EXT = "/run/media/user/PNYRP60PSSD"


def measured(test: str, volume: str, gbs: float, **extra: object) -> Measurement:
    fields: dict[str, object] = {
        "id": f"{test}@{volume}",
        "test": test,
        "volume": volume,
        "bytes": int(gbs * 4e9),
        "seconds": 4.0,
        "gbs": gbs,
        "cache": "direct",
        "spread": 0.1,
    }
    return Measurement.model_validate({**fields, **extra})


def test_load_time_prefers_model_reads_then_seq_then_the_link() -> None:
    run = scan(probe=reference_probe(), finders={})
    model = Item(id="m", names=["gemma4:31b"], bytes=19_000_000_000)
    by_link = load_time(model, EXT, run)
    assert by_link is not None and by_link.basis == "expected link speed" and by_link.cites == []
    run.measurements = [measured("seq-read", EXT, 0.97)]
    by_seq = load_time(model, EXT, run)
    assert by_seq is not None and by_seq.basis == "measured seq-read" and round(by_seq.seconds) == 20
    assert by_seq.note == DISK_ONLY and by_seq.low < by_seq.seconds < by_seq.high
    run.measurements.append(measured("model-read", "/", 2.2))
    by_model = load_time(model, "/", run)
    assert by_model is not None and by_model.basis == "measured model-read" and round(by_model.seconds) == 9
    assert load_time(Item(id="x", names=["x"]), "/", run) is None
    assert read_speed(run, "/nowhere") is None


def test_cached_or_partial_results_are_not_used() -> None:
    run = scan(probe=reference_probe(), finders={})
    run.measurements = [
        measured("seq-read", "/", 9.0, cache="possibly-cached"),
        measured("seq-read", "/", 5.0, partial=True),
    ]
    speed = read_speed(run, "/")
    assert speed is not None and speed[2] == "expected link speed"


def test_copy_time_and_warm_loads() -> None:
    run = scan(probe=reference_probe(), finders={})
    store = Store(id="ollama", title="o", bytes=388_000_000_000)
    assert copy_time(store, "/", EXT, run) is None
    run.measurements = [measured("seq-read", "/", 2.2), measured("sustained-write", EXT, 0.89)]
    estimate = copy_time(store, "/", EXT, run)
    assert estimate is not None and round(estimate.seconds / 60) == 7
    assert warm(Item(id="a", names=["a"], bytes=10), run, 100) and not warm(
        Item(id="a", names=["a"]), run, 100
    )
