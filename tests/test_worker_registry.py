import pytest

from worker.registry import (
    PermanentJobError,
    _literature_search,
    _poll_download,
    _publish_draft,
    _publish_video,
    _summary_job,
)


@pytest.mark.parametrize(
    ("handler", "payload"),
    [
        (_publish_draft, {"record_id": "bad"}),
        (_publish_video, {}),
        (_literature_search, {"request": {"mode": "years"}}),
        (_poll_download, {"task_id": "task-only"}),
        (_summary_job, {}),
        (_publish_draft, []),
    ],
)
def test_invalid_job_parameters_are_permanent(handler, payload):
    with pytest.raises(PermanentJobError):
        handler(payload, "job-id")
