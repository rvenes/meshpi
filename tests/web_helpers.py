from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "meshpi" / "web_assets"


def javascript_asset(name: str) -> str:
    """Load the shared text helper before an isolated browser component."""
    return ((ASSETS / "text.js").read_text(encoding="utf-8") + "\n"
            + (ASSETS / name).read_text(encoding="utf-8"))
