from hone_drive import text


def test_sizes_speeds_and_times() -> None:
    assert text.size(388_439_697_430) == "388 GB"
    assert text.size(1_500_000_000) == "1.5 GB"
    assert text.size(2_000_000_000_000) == "2.0 TB"
    assert text.size(12) == "12 B"
    assert text.size(None) == "?"
    assert text.speed(2.2) == "2.20 GB/s"
    assert text.speed(0.21) == "210 MB/s"
    assert text.speed(None) == "?"
    assert text.seconds(8.6) == "8.6 s"
    assert text.seconds(19.5) == "20 s"
    assert text.seconds(600) == "10 min"
    assert text.seconds(7200) == "2.0 h"
    assert text.seconds(None) == "?"
    assert text.percent(0.94) == "94%"
