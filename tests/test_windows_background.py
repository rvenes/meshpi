from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from meshpi import windows_background


def test_background_launcher_preserves_arguments_and_disables_console(monkeypatch):
    launch = Mock()
    supervisor = "C:\\Brukar Øyvind [test]\\meshpi-supervisor.ps1"
    monkeypatch.setattr(sys, "argv", ["launcher", supervisor])
    monkeypatch.setattr(windows_background, "windows_powershell_path", lambda: "powershell.exe")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "Popen", launch)
    windows_background.main()
    args, kwargs = launch.call_args
    assert args[0][-2:] == ["-File", supervisor]
    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL


@pytest.mark.skipif(os.name != "nt", reason="krev Windows")
def test_pythonw_startup_has_no_console(tmp_path):
    supervisor = tmp_path / "prosessvakt Ø [test].ps1"
    result = tmp_path / "console.txt"
    supervisor.write_text(
        'Add-Type -TypeDefinition \'using System; using System.Runtime.InteropServices; '
        'public class ConsoleProbe { [DllImport("kernel32.dll")] '
        "public static extern IntPtr GetConsoleWindow(); }'\n"
        f"[IO.File]::WriteAllText('{str(result).replace(chr(39), chr(39) * 2)}', "
        "[ConsoleProbe]::GetConsoleWindow().ToInt64().ToString())\n",
        encoding="utf-8-sig",
    )
    process = subprocess.Popen(
        [str(Path(sys.executable).with_name("pythonw.exe")), "-m",
         "meshpi.windows_background", str(supervisor)],
    )
    assert process.wait(timeout=20) == 0
    deadline = time.monotonic() + 20
    while not result.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert result.read_text() == "0"
