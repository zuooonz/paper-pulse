import pandas as pd
import yaml


def test_excel_import_uses_configured_neutral_column_mapping(tmp_path):
    from scripts.import_excel_papers import _build_paper_data_from_excel, load_import_config

    config_path = tmp_path / "imports.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "excel": {
                    "sheet_name": "Papers",
                    "columns": {
                        "doi": ["Identifier"],
                        "title": ["Paper Name"],
                        "journal": ["Publication"],
                        "year": ["Pub Year"],
                    },
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_import_config(config_path)
    row = pd.Series(
        {
            "Identifier": "https://doi.org/10.1000/example",
            "Paper Name": "A fictional study",
            "Publication": "Journal of Imaginary Research",
            "Pub Year": 2024,
        }
    )

    data = _build_paper_data_from_excel(row, "fixture", 2, config["columns"])

    assert config["sheet_name"] == "Papers"
    assert data["doi"] == "10.1000/example"
    assert data["title"] == "A fictional study"
    assert data["journal_name"] == "Journal of Imaginary Research"
    assert data["year"] == 2024


def test_excel_import_default_config_supports_wos_and_endnote_headers():
    from scripts.import_excel_papers import load_import_config

    config = load_import_config()

    assert config["sheet_name"] == "Sheet1"
    assert "Article Title" in config["columns"]["title"]
    assert "Title" in config["columns"]["title"]
    assert "Source Title" in config["columns"]["journal"]
    assert "Secondary Title" in config["columns"]["journal"]
