from api.routers.config_options import build_config_options


def test_config_options_exposes_generation_scope_default_and_override():
    default = build_config_options({})
    assert default["generation"]["require_target_entity"] is True

    configured = build_config_options({"domain": {"generation": {"require_target_entity": False}}})
    assert configured["generation"]["require_target_entity"] is False
