from html import unescape
from pathlib import Path

from meshpi.cli import COMMANDS
from meshpi.tui import HelpScreen

ROOT = Path(__file__).resolve().parents[1]


def test_readme_and_website_list_every_cli_command_and_global_option():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    website = unescape((ROOT / "website" / "index.html").read_text(encoding="utf-8"))

    for command in COMMANDS:
        assert f"meshpi {command}" in readme
        assert f"meshpi {command}" in website
    for option in ("--help", "--version", "--env-file", "--json"):
        assert option in readme
        assert option in website


def test_readme_and_website_include_every_f1_shortcut():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    website = unescape(
        (ROOT / "website" / "index.html").read_text(encoding="utf-8")
    ).casefold()

    for key, description in HelpScreen.SHORTCUTS:
        assert key.casefold() in readme
        assert description.casefold() in readme
        assert key.casefold() in website
        assert description.casefold() in website
