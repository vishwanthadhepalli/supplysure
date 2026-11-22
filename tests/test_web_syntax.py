"""Catch JavaScript syntax errors in the web app (a broken app.js renders a blank page).

Uses node if installed, else macOS JavaScriptCore; skips if neither is available.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "app" / "web" / "app.js"
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")


def test_app_js_parses():
    node = shutil.which("node")
    if node:
        cmd = [node, "--check", str(APP_JS)]
    elif JSC.exists():
        cmd = [str(JSC), "-e", f"checkSyntax({str(APP_JS)!r})"]
    else:
        pytest.skip("no JavaScript engine available")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output = (result.stdout + result.stderr).strip()
    assert result.returncode == 0 and "SyntaxError" not in output, output
