from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
USER_FACING_MODULES = (
    "ble.py",
    "cli.py",
    "client.py",
    "connect_tui.py",
    "connections.py",
    "doctor.py",
    "lifecycle.py",
    "node_actions.py",
    "platform_service.py",
    "service.py",
    "signing.py",
    "transports.py",
    "tui.py",
    "update.py",
    "versions.py",
)
USER_FACING_CALLS = {
    "Button",
    "CLIError",
    "CLIUnavailableError",
    "Label",
    "Option",
    "RuntimeError",
    "SignatureError",
    "Static",
    "ValueError",
    "notify",
    "print",
}


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def test_user_facing_python_text_uses_translation_keys() -> None:
    """Catch new literal UI/error text before it bypasses the catalogs."""

    violations: list[str] = []
    for filename in USER_FACING_MODULES:
        path = ROOT / "meshpi" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) not in USER_FACING_CALLS:
                continue
            for argument in node.args[:1]:
                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and argument.value not in {"Nynorsk", "English"}
                    and any(character.isalpha() for character in argument.value)
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{argument.lineno}: "
                        f"{argument.value!r}"
                    )

    assert not violations, "Literal user-facing text found:\n" + "\n".join(violations)
