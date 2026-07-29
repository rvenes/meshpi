import re
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


def test_public_examples_do_not_contain_development_network_addresses():
    public_files = [
        ROOT / "README.md",
        ROOT / "meshpi" / "models.py",
        *(ROOT / "website").glob("*"),
        *(ROOT / "installers").glob("*"),
    ]

    for path in public_files:
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8-sig")
        assert re.search(r"\b10\.0\.0\.\d{1,3}\b", source) is None, path
