"""Keep strings.json and translations/en.json in sync."""

from __future__ import annotations

import json
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "notification_api"


def test_en_translation_matches_strings() -> None:
    """Custom integrations ship en.json as-is, so it must equal strings.json."""
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert english == strings
