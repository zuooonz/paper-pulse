import logging

from api.error_responses import internal_http_error


def test_internal_http_error_logs_detail_but_returns_generic_message(caplog):
    logger = logging.getLogger("test.internal-error")
    secret_detail = "database failed at /private/path with token=hidden"
    try:
        raise RuntimeError(secret_detail)
    except RuntimeError as exc:
        response_error = internal_http_error(
            logger,
            "test operation",
            exc,
            status_code=503,
            detail="服务暂不可用，请稍后重试",
        )

    assert response_error.status_code == 503
    assert response_error.detail == "服务暂不可用，请稍后重试"
    assert secret_detail not in response_error.detail
    assert secret_detail in caplog.text
