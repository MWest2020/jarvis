import pytest

from agent_host.turnlock import Busy, acquire_turn_lock, available_memory_mb


def test_a_second_acquire_is_busy_while_the_first_holds_it(tmp_path):
    lock_path = tmp_path / "turn.lock"
    with acquire_turn_lock(lock_path):
        with pytest.raises(Busy):
            with acquire_turn_lock(lock_path):
                pass  # pragma: no cover


def test_the_lock_is_free_again_after_release(tmp_path):
    lock_path = tmp_path / "turn.lock"
    with acquire_turn_lock(lock_path):
        pass
    with acquire_turn_lock(lock_path):
        pass  # no Busy this time


def test_available_memory_reads_mem_available(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       4040000 kB\nMemAvailable:    614400 kB\n")
    assert available_memory_mb(meminfo) == 600.0


def test_available_memory_fails_open_when_unreadable(tmp_path):
    assert available_memory_mb(tmp_path / "does-not-exist") == float("inf")


def test_available_memory_fails_open_when_the_field_is_missing(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 4040000 kB\n")
    assert available_memory_mb(meminfo) == float("inf")
