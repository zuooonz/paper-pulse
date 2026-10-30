import subprocess
import sys

from core.pdf_extractor import _terminate_process_tree


def test_pdf_extractor_cleanup_waits_for_child_process():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    _terminate_process_tree(process)

    assert process.poll() is not None
