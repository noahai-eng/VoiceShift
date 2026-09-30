#!/usr/bin/env python3
"""Tests für HotkeyListener – Loslassen darf nie verloren gehen.

Hintergrund: Ob die Aufnahme endet, hing allein am flagsChanged-Event des
Loslassens. macOS schaltet den Event-Tap aber bei Last ab (DisabledByTimeout)
oder unterdrückt Events (Passwortfelder, Sperrbildschirm). Fiel das Loslassen
in so ein Fenster, lief die Aufnahme endlos weiter – und solange sie lief,
wurde Shift aus jedem Tastendruck entfernt (keine Großbuchstaben mehr).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from hotkey import HotkeyListener  # noqa: E402


class FakeListener(HotkeyListener):
    def __init__(self):
        self.events = []
        super().__init__(on_press=lambda: self.events.append("press"),
                         on_release=lambda: self.events.append("release"))
        self.physically_held = True

    def _modifiers_held(self):
        return self.physically_held


def test_druecken_und_loslassen_normal():
    hk = FakeListener()
    hk._on_flags(ctrl=True, shift=True)
    hk._on_flags(ctrl=True, shift=True)     # Wiederholung: kein zweites press
    hk._on_flags(ctrl=False, shift=True)
    assert hk.events == ["press", "release"]


def test_verpasstes_loslassen_wird_per_abgleich_erkannt():
    """DER Bug: Release-Event kam nie an → Aufnahme lief endlos."""
    hk = FakeListener()
    hk._on_flags(ctrl=True, shift=True)

    hk.physically_held = False     # Tasten sind los, aber kein Event kam
    hk._resync()

    assert hk.events == ["press", "release"]


def test_abgleich_laesst_gehaltene_tasten_in_ruhe():
    hk = FakeListener()
    hk._on_flags(ctrl=True, shift=True)
    hk._resync()
    assert hk.events == ["press"]


def test_abgleich_ohne_aktive_aufnahme_tut_nichts():
    hk = FakeListener()
    hk.physically_held = False
    hk._resync()
    assert hk.events == []


def test_hintergrund_abgleich_beendet_haengende_aufnahme():
    hk = FakeListener()
    hk._RESYNC_INTERVAL = 0.02
    hk._on_flags(ctrl=True, shift=True)
    hk.physically_held = False

    deadline = time.time() + 1
    while hk.events != ["press", "release"] and time.time() < deadline:
        time.sleep(0.01)
    assert hk.events == ["press", "release"]


def test_spaetes_release_event_nach_abgleich_loest_nichts_doppelt_aus():
    hk = FakeListener()
    hk._on_flags(ctrl=True, shift=True)
    hk.physically_held = False
    hk._resync()
    hk._on_flags(ctrl=False, shift=False)   # das verspätete echte Event
    assert hk.events == ["press", "release"]
