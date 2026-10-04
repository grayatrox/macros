"""The shared Win32 named-mutex bindings (OP #703).

Real Windows mutexes, each with a unique name. Mutex ownership belongs to a
thread, so contention is tested from a second thread.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest

from pluginhost import named_mutex

WAIT_OBJECT_0 = 0


@pytest.fixture
def name() -> str:
    return f"Local\\pluginhost-test-{uuid.uuid4()}"


@pytest.fixture
def handle(name: str) -> Iterator[int]:
    opened, existed = named_mutex.create(name)
    assert not existed
    yield opened
    named_mutex.close(opened)


def _wait_on_another_thread(name: str) -> int:
    results: list[int] = []

    def probe() -> None:
        other, _existed = named_mutex.create(name)
        try:
            result = named_mutex.wait(other, 0)
            if result == WAIT_OBJECT_0:
                named_mutex.release(other)
            results.append(result)
        finally:
            named_mutex.close(other)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=5)
    return results[0]


def test_create_reports_whether_the_name_already_existed(name: str, handle: int) -> None:
    second, existed = named_mutex.create(name)
    named_mutex.close(second)
    assert existed


def test_closing_every_handle_frees_the_name(name: str) -> None:
    first, _ = named_mutex.create(name)
    named_mutex.close(first)
    again, existed = named_mutex.create(name)
    named_mutex.close(again)
    assert not existed


def test_wait_owns_a_free_mutex(name: str, handle: int) -> None:
    assert named_mutex.wait(handle, 0) == WAIT_OBJECT_0
    named_mutex.release(handle)


def test_wait_times_out_while_another_thread_owns_it(name: str, handle: int) -> None:
    assert named_mutex.wait(handle, 0) == WAIT_OBJECT_0
    try:
        assert _wait_on_another_thread(name) == named_mutex.WAIT_TIMEOUT
    finally:
        named_mutex.release(handle)


def test_release_lets_another_thread_own_it(name: str, handle: int) -> None:
    assert named_mutex.wait(handle, 0) == WAIT_OBJECT_0
    named_mutex.release(handle)
    assert _wait_on_another_thread(name) == WAIT_OBJECT_0


def test_a_name_windows_rejects_raises() -> None:
    # Only the Local and Global namespaces exist, so CreateMutexW itself fails.
    with pytest.raises(OSError) as raised:
        named_mutex.create(f"NoSuchNamespace\\{uuid.uuid4()}")
    assert raised.value.winerror == 3  # ERROR_PATH_NOT_FOUND
