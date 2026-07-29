#!/usr/bin/env python3
"""
Berechtigungen prüfen – Accessibility und Mikrofon
"""
import subprocess
import rumps

def check_and_request():
    lines = ["Berechtigungs-Status:\n"]

    # Accessibility testen
    result = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to get name of first process'],
        capture_output=True
    )
    if result.returncode == 0:
        lines.append("✅  Bedienungshilfen: OK")
    else:
        lines.append("❌  Bedienungshilfen: FEHLT")
        lines.append("    Systemeinstellungen > Datenschutz")
        lines.append("    > Bedienungshilfen > Terminal ✓")

    lines.append("")
    lines.append("🎤  Mikrofon:")
    lines.append("    Systemeinstellungen > Datenschutz")
    lines.append("    > Mikrofon > Terminal ✓")
    lines.append("")
    lines.append("Tipp: App danach neu starten.")

    response = rumps.alert(
        title="🔐  Berechtigungen prüfen",
        message="\n".join(lines),
        ok="Einstellungen öffnen",
        cancel="OK"
    )
    if response == 1:
        subprocess.run([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy"
        ])
