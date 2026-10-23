import os

from app.utils import memory


def test_rss_reads_resident_pages_from_statm(tmp_path, monkeypatch):
    statm = tmp_path / "statm"
    # size resident shared text lib data dt
    statm.write_text("90000 51200 3000 1 0 40000 0\n")
    monkeypatch.setattr(memory, "STATM_PATH", str(statm))

    expected = 51200 * os.sysconf("SC_PAGE_SIZE") / memory.MB
    assert memory.rss_mb() == expected


def test_rss_is_none_without_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "STATM_PATH", str(tmp_path / "missing"))
    assert memory.rss_mb() is None


def test_rss_is_none_for_an_unreadable_statm(tmp_path, monkeypatch):
    statm = tmp_path / "statm"
    statm.write_text("garbage\n")
    monkeypatch.setattr(memory, "STATM_PATH", str(statm))
    assert memory.rss_mb() is None


class FakeResource:
    RUSAGE_SELF = 0

    def __init__(self, maxrss):
        self._maxrss = maxrss

    def getrusage(self, who):
        return type("Usage", (), {"ru_maxrss": self._maxrss})()


def test_peak_is_kilobytes_on_linux(monkeypatch):
    monkeypatch.setattr(memory, "resource", FakeResource(300 * 1024))
    monkeypatch.setattr(memory.sys, "platform", "linux")
    assert memory.peak_rss_mb() == 300


def test_peak_is_bytes_on_macos(monkeypatch):
    monkeypatch.setattr(memory, "resource", FakeResource(300 * memory.MB))
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    assert memory.peak_rss_mb() == 300


def test_peak_is_none_without_resource(monkeypatch):
    monkeypatch.setattr(memory, "resource", None)
    assert memory.peak_rss_mb() is None
