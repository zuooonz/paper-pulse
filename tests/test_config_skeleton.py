"""Tests for configuration skeleton files."""

import yaml
from pathlib import Path


CONFIG_DIR = Path(__file__).parent.parent / "config"


def test_runtime_example_yaml_loadable():
    """Test the shareable runtime example is valid YAML."""
    path = CONFIG_DIR / "runtime.example.yaml"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data is not None
    assert "provider" in data
    assert {"app", "storage", "cors_origins", "auth"}.issubset(data)
    assert data["auth"]["allow_plaintext_passwords"] is False
    assert set(data["auth"]["local_users"]) == {"admin", "reviewer", "viewer", "limited"}
    for user in data["auth"]["local_users"].values():
        assert "${" in user["password"]

    def assert_sensitive_values_are_placeholders(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                key_lower = str(key).lower()
                if key_lower != "allow_plaintext_passwords" and any(marker in key_lower for marker in ("password", "secret", "api_key", "api_token", "mailto", "email", "_ak")):
                    assert nested == "" or (isinstance(nested, str) and nested.startswith("${"))
                assert_sensitive_values_are_placeholders(nested)
        elif isinstance(value, list):
            for item in value:
                assert_sensitive_values_are_placeholders(item)

    assert_sensitive_values_are_placeholders(data)


def test_env_example_has_safe_values_and_key_groups():
    path = CONFIG_DIR.parent / ".env.example"
    assert path.exists()
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name] = value

    assert {
        "APP_TIMEZONE", "DATABASE_URL", "JWT_SECRET", "LLM_API_KEY",
        "REFERENCE_CONFIG", "JOURNAL_METRICS_DB", "STORAGE_ROOT", "PDF_ROOT", "OUTPUT_ROOT",
    }.issubset(values)
    assert values["JWT_SECRET"] == ""
    assert values["LLM_API_KEY"] == ""
    assert values["DINGTALK_CLIENT_SECRET"] == ""
    assert values["LOCAL_ADMIN_PASSWORD"] == ""
    assert values["ALLOW_PLAINTEXT_PASSWORDS"] == "false"
    assert values["CREDENTIALS_ENCRYPTION_KEY"] == ""


def test_models_yaml_loadable():
    """Test models.yaml is valid YAML."""
    path = CONFIG_DIR / "models.yaml"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data is not None
    assert "models" in data


def test_reference_and_import_configs_loadable():
    for name, required in (
        ("reference.yaml", "journal_metrics"),
        ("imports.yaml", "excel"),
    ):
        with (CONFIG_DIR / name).open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        assert isinstance(data, dict)
        assert required in data


def test_prompts_yaml_loadable():
    """Test prompts.yaml is valid YAML."""
    path = CONFIG_DIR / "prompts.yaml"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data is not None


def test_profile_detail_yaml_loadable():
    """Test profile_detail.yaml is valid YAML."""
    path = CONFIG_DIR / "profile_detail.yaml"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data is not None


def test_profile_collection_yaml_loadable():
    """Test profile_collection.yaml is valid YAML."""
    path = CONFIG_DIR / "profile_collection.yaml"
    assert path.exists()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data is not None


# Skip module path test for now - requires proper package installation
# def test_cli_help_runs():
#     """Test that CLI help still works (no import breakage)."""
#     import subprocess
#     result = subprocess.run(
#         ["python", "cli.py", "--help"],
#         capture_output=True,
#         text=True
#     )
#     assert result.returncode == 0
#     assert "usage" in result.stdout.lower()
