#!/usr/bin/env python3
"""Tests für SerialPipeline – die Kernregel: NIE zwei Verarbeitungen gleichzeitig.

Hintergrund (aus /tmp/voiceshift.out.log, Zeilen 3753–3775 belegt):
  Jede Aufnahme startete einen eigenen Thread für Transkription+Injection.
  Der Injector sichert aber das Clipboard, überschreibt es mit dem Diktat,
  fügt ein und schreibt das alte zurück. Laufen zwei davon gleichzeitig,
  verschränken sich diese Abschnitte: Thread A schreibt den Text von Thread B
  als "Original" zurück, während die Ziel-App noch gar nicht gelesen hat –
  und im Terminal landet ein altes Diktat.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pipeline import SerialPipeline  # noqa: E402


class OverlapDetector:
    """Handler, der festhält, ob sich zwei Läufe zeitlich überschnitten haben."""

    def __init__(self, work_time=0.05):
        self.work_time = work_time
        self.order = []
        self.overlapped = False
        self._active = 0
        self._lock = threading.Lock()
        self.done = threading.Event()
        self._expected = None

    def expect(self, n):
        self._expected = n

    def __call__(self, item):
        with self._lock:
            self._active += 1
            if self._active > 1:
                self.overlapped = True
        # Echte Arbeit simulieren: hier läuft im Ernstfall der kritische
        # Clipboard-Abschnitt des Injectors.
        time.sleep(self.work_time)
        with self._lock:
            self._active -= 1
            self.order.append(item)
            if self._expected is not None and len(self.order) >= self._expected:
                self.done.set()


def test_zwei_eintraege_laufen_nie_gleichzeitig():
    """DER Bug: überlappende Injections verschränken den Clipboard-Abschnitt."""
    handler = OverlapDetector()
    handler.expect(2)
    pipe = SerialPipeline(handler)
    pipe.start()

    pipe.submit("A")
    pipe.submit("B")
    assert handler.done.wait(timeout=5), "Verarbeitung lief nicht zu Ende"

    assert handler.overlapped is False, (
        "Zwei Verarbeitungen liefen gleichzeitig – der Clipboard-Abschnitt "
        "des Injectors kann sich verschränken")


def test_reihenfolge_bleibt_die_gesprochene():
    """FIFO: Aufnahmen sind sequentiell, das Ergebnis muss es auch sein.

    Im Log drängelte sich ein kurzes '[MUSIK]' vor ein 701-Zeichen-Diktat,
    weil es schneller transkribiert war.
    """
    handler = OverlapDetector(work_time=0.01)
    handler.expect(5)
    pipe = SerialPipeline(handler)
    pipe.start()

    for item in ["erst", "zweit", "dritt", "viert", "fuenft"]:
        pipe.submit(item)
    assert handler.done.wait(timeout=5), "Verarbeitung lief nicht zu Ende"

    assert handler.order == ["erst", "zweit", "dritt", "viert", "fuenft"]


def test_fehler_stoppt_die_pipeline_nicht():
    """Ein gescheitertes Diktat darf nicht alle folgenden blockieren."""
    seen = []
    done = threading.Event()

    def handler(item):
        if item == "kaputt":
            raise RuntimeError("Transkription fehlgeschlagen")
        seen.append(item)
        if len(seen) == 2:
            done.set()

    pipe = SerialPipeline(handler)
    pipe.start()

    pipe.submit("gut1")
    pipe.submit("kaputt")
    pipe.submit("gut2")
    assert done.wait(timeout=5), "Pipeline blieb nach dem Fehler stehen"

    assert seen == ["gut1", "gut2"]


def test_busy_bis_der_letzte_eintrag_fertig_ist():
    """Der Watchdog-Neustart wartet darauf – sonst stirbt ein Diktat mitten
    in der Transkription."""
    gate = threading.Event()
    pipe = SerialPipeline(lambda item: gate.wait(2))
    pipe.start()
    assert not pipe.busy()

    pipe.submit("A")
    assert pipe.busy()

    gate.set()
    deadline = time.time() + 2
    while pipe.busy() and time.time() < deadline:
        time.sleep(0.01)
    assert not pipe.busy()
