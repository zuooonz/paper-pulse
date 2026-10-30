import importlib

import pytest
from defusedxml.common import DefusedXmlException


@pytest.mark.parametrize(
    "module_name",
    [
        "core.date_extractor",
        "core.pubmed_affiliation",
        "core.received_date_extractor",
    ],
)
def test_pubmed_response_parsers_reject_entity_declarations(module_name):
    module = importlib.import_module(module_name)
    payload = b'<!DOCTYPE root [<!ENTITY item "blocked">]><root>&item;</root>'

    with pytest.raises(DefusedXmlException):
        module.ET.fromstring(payload)
