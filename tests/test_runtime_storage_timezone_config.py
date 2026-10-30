import datetime as dt
from zoneinfo import ZoneInfo

import yaml


def test_timezone_env_overrides_runtime_configuration(monkeypatch, tmp_path):
    from core.service_config import app_timezone
    from paper_library.time_utils import to_local

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.yaml").write_text(
        yaml.safe_dump({"app": {"timezone": "Europe/Paris"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_TIMEZONE", raising=False)

    assert app_timezone() == "Europe/Paris"
    assert to_local(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)).tzinfo == ZoneInfo("Europe/Paris")

    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    assert app_timezone() == "UTC"
    assert to_local(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)).utcoffset() == dt.timedelta(0)


def test_storage_roots_respect_yaml_and_environment_overrides(monkeypatch, tmp_path):
    from tools import storage

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.yaml").write_text(
        yaml.safe_dump(
            {
                "storage": {
                    "root": "archive",
                    "pdf_root": "pdf-files",
                    "images_root": "figures",
                    "cache_root": "cache-files",
                    "temp_root": "scratch",
                    "output_root": "reports",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    monkeypatch.delenv("PDF_ROOT", raising=False)
    monkeypatch.delenv("OUTPUT_ROOT", raising=False)

    assert storage.get_storage_root(tmp_path) == (tmp_path / "archive").resolve()
    assert storage.get_pdf_dir(tmp_path) == (tmp_path / "archive" / "pdf-files").resolve()
    assert storage.get_output_dir(tmp_path) == (tmp_path / "reports").resolve()

    monkeypatch.setenv("STORAGE_ROOT", "custom-data")
    monkeypatch.setenv("PDF_ROOT", "custom-pdf")
    monkeypatch.setenv("OUTPUT_ROOT", "custom-output")
    assert storage.get_storage_root(tmp_path) == (tmp_path / "custom-data").resolve()
    assert storage.get_pdf_dir(tmp_path) == (tmp_path / "custom-pdf").resolve()
    assert storage.get_temp_dir(tmp_path) == (tmp_path / "custom-data" / "scratch").resolve()
    assert storage.get_output_dir(tmp_path) == (tmp_path / "custom-output").resolve()


def test_video_storage_uses_workspace_for_external_absolute_root(monkeypatch, tmp_path):
    from api.routers import videos
    from tools.storage import ensure_relative_path

    workspace = tmp_path / "workspace"
    external_root = tmp_path / "external-storage"
    workspace.mkdir()
    monkeypatch.setattr(videos.settings, "workspace", str(workspace))
    monkeypatch.setenv("STORAGE_ROOT", str(external_root))

    video_dir = videos._video_dir()
    video_path = video_dir / "clip.mp4"

    assert video_dir == external_root / "videos"
    assert ensure_relative_path(video_path, videos.settings.workspace) == str(video_path)
