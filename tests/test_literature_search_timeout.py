import subprocess
import sys

import pytest

from api.routers import literature_search


def test_literature_search_process_streams_output(tmp_path):
    lines = []
    return_code = literature_search._run_streaming_process(
        [sys.executable, "-c", "print('first'); print('second')"],
        cwd=tmp_path,
        env={},
        on_line=lines.append,
        timeout_seconds=5,
    )

    assert return_code == 0
    assert lines == ["first\n", "second\n"]


def test_literature_search_timeout_terminates_and_waits_for_child(monkeypatch, tmp_path):
    original_popen = subprocess.Popen
    processes = []

    def capture_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(literature_search.subprocess, "Popen", capture_process)
    with pytest.raises(subprocess.TimeoutExpired):
        literature_search._run_streaming_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env={},
            on_line=lambda _line: None,
            timeout_seconds=0.2,
        )

    assert len(processes) == 1
    assert processes[0].poll() is not None
