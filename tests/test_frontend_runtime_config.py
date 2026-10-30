from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nextauth_server_calls_use_configured_backend_url():
    source = (ROOT / "frontend/src/auth.ts").read_text(encoding="utf-8")

    assert 'process.env.BACKEND_API_URL || "http://localhost:8000"' in source
    assert source.count("fetch(`${backendApiUrl}/api/auth/") == 2
    assert 'fetch("http://localhost:8000/api/auth/' not in source


def test_frontend_datetime_timezone_is_configurable_and_wired_through_compose():
    datetime_source = (ROOT / "frontend/src/lib/datetime.ts").read_text(encoding="utf-8")
    compose_source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")
    frontend_env = (ROOT / "frontend/.env.example").read_text(encoding="utf-8")
    root_env = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'process.env.NEXT_PUBLIC_APP_TIMEZONE || "Asia/Shanghai"' in datetime_source
    assert datetime_source.count("timeZone: appTimezone") == 2
    assert "NEXT_PUBLIC_APP_TIMEZONE: ${NEXT_PUBLIC_APP_TIMEZONE:-Asia/Shanghai}" in compose_source
    assert "NEXT_PUBLIC_APP_TIMEZONE=${NEXT_PUBLIC_APP_TIMEZONE}" in dockerfile
    assert "NEXT_PUBLIC_APP_TIMEZONE=Asia/Shanghai" in frontend_env
    assert "NEXT_PUBLIC_APP_TIMEZONE=Asia/Shanghai" in root_env
    assert "`NEXT_PUBLIC_APP_TIMEZONE`" in readme
