from __future__ import annotations

from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PACKAGE_ROOT / "spec" / "pm-api-openapi-v0.1.yaml"
DOCS_MIRROR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "reference"
    / "pm-api-openapi-v0.1.yaml"
)


def test_contract_spec_is_present_in_the_package() -> None:
    assert SPEC_PATH.is_file()


def test_opennomos_docs_mirror_matches_when_present() -> None:
    if not DOCS_MIRROR.is_file():
        pytest.skip("OpenNomos docs mirror is not present in a standalone checkout.")
    assert DOCS_MIRROR.read_bytes() == SPEC_PATH.read_bytes()
