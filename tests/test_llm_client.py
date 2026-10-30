from unittest import mock

import pytest

from core.llm_client import OpenAICompatibleClient


def _response(data, status_code=200):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = data
    response.text = str(data)
    response.raise_for_status.return_value = None
    return response


@mock.patch("core.llm_client.time.sleep")
@mock.patch("core.llm_client.requests.post")
def test_chat_retries_200_response_without_choices(post, sleep):
    post.side_effect = [
        _response({"code": "ServiceUnavailable", "message": "try again"}),
        _response({"choices": [{"message": {"content": "OK"}}]}),
    ]
    client = OpenAICompatibleClient("https://example.test/v1", "secret", max_retries=2)

    assert client.chat("model", [{"role": "user", "content": "test"}]) == "OK"
    assert post.call_count == 2
    sleep.assert_called_once_with(5)


@mock.patch("core.llm_client.time.sleep")
@mock.patch("core.llm_client.requests.post")
def test_chat_reports_provider_error_when_choices_never_arrive(post, sleep):
    post.return_value = _response({"error": {"code": "QuotaExceeded", "message": "quota exhausted"}})
    client = OpenAICompatibleClient("https://example.test/v1", "secret", max_retries=2)

    with pytest.raises(RuntimeError, match="QuotaExceeded: quota exhausted"):
        client.chat("model", [{"role": "user", "content": "test"}])

    assert post.call_count == 2
