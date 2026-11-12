import hashlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "spec" / "providers" / "spec-manifest.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


@pytest.mark.live
def test_provider_specs_match_manifest() -> None:
    manifest = _load_manifest()
    import urllib.request

    for name, entry in manifest["providers"].items():
        if entry["status"] != "available":
            continue
        url = entry["url"]
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        actual = hashlib.sha256(data).hexdigest()
        assert actual == entry["sha256"], (
            f"Provider spec changed for {name}: expected {entry['sha256']}, got {actual}. "
            "Re-run scripts/generate-provider-clients.sh after updating the manifest."
        )
