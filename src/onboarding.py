#!/usr/bin/env python3
"""
Onboarding – 4-Schritt Einrichtungsassistent (beim ersten Start)
"""
import rumps
import subprocess

STEPS = [
    {
        "title":   "Willkommen bei VoiceShift 🎙️  (1/4)",
        "message": (
            "VoiceShift läuft in deiner Menüleiste und schreibt\n"
            "deine Sprache direkt in jedes Textfeld auf dem Mac.\n\n"
            "Hotkey:  Ctrl + Shift\n"
            "  → Halten = Aufnahme läuft  (🔴 in Menüleiste)\n"
            "  → Loslassen = Text wird eingefügt\n\n"
            "Kein Copy-Paste, kein Cloud-Dienst.\n"
            "Alles läuft lokal auf deinem M1."
        ),
        "ok":     "Weiter →",
        "cancel": None,
        "action": None,
    },
    {
        "title":   "Mikrofon-Berechtigung 🎤  (2/4)",
        "message": (
            "macOS benötigt deine Erlaubnis für das Mikrofon.\n\n"
            "1.  Systemeinstellungen öffnen\n"
            "2.  Datenschutz & Sicherheit\n"
            "3.  Mikrofon\n"
            "4.  VoiceShift aktivieren ✓\n\n"
            "Der Button unten öffnet die Einstellungen direkt."
        ),
        "ok":     "Einstellungen öffnen →",
        "cancel": "Überspringen",
        "action": "privacy",
    },
    {
        "title":   "Bedienungshilfen 🖥️  (3/4)",
        "message": (
            "Damit Text direkt ins aktive Textfeld eingefügt\n"
            "werden kann, braucht die App Accessibility-Zugriff.\n\n"
            "1.  Systemeinstellungen öffnen\n"
            "2.  Datenschutz & Sicherheit\n"
            "3.  Bedienungshilfen\n"
            "4.  VoiceShift aktivieren ✓\n\n"
            "⚠️  Ohne diese Berechtigung wird kein Text eingefügt."
        ),
        "ok":     "Einstellungen öffnen →",
        "cancel": "Überspringen",
        "action": "privacy",
    },
    {
        "title":   "Fertig – los geht's! 🚀  (4/4)",
        "message": (
            "VoiceShift ist einsatzbereit.\n\n"
            "Ctrl + Shift  →  Aufnahme starten/stoppen\n"
            "🔴 in Menüleiste      →  Aufnahme läuft\n"
            "⏳ in Menüleiste      →  Wird verarbeitet\n"
            "🎙️  in Menüleiste     →  Bereit\n\n"
            "Modi (Phase 2 mit Claude API):\n"
            "  Normal      →  reiner transkribierter Text\n"
            "  Formell     →  professionelle Umformulierung\n"
            "  Übersetzen  →  Deutsch → Englisch\n"
            "  Struktur    →  Bullet Points / Meeting Notes"
        ),
        "ok":     "Los geht's!",
        "cancel": None,
        "action": None,
    },
]

def show_onboarding_window():
    for step in STEPS:
        response = rumps.alert(
            title=step["title"],
            message=step["message"],
            ok=step["ok"],
            cancel=step["cancel"],
        )
        if step["action"] == "privacy" and response == 1:
            subprocess.run([
                "open",
                "x-apple.systempreferences:"
                "com.apple.preference.security?Privacy"
            ])
        # response == 0 → Cancel / Überspringen → weiter mit nächstem Schritt
