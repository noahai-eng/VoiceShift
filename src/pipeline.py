#!/usr/bin/env python3
"""
SerialPipeline – arbeitet eingereihte Aufgaben streng nacheinander ab.

Warum das sein muss (belegt in /tmp/voiceshift.out.log, Zeilen 3753–3775):
Früher startete jede Aufnahme ihren eigenen Thread für Transkription und
Injection. Der Injector führt dabei einen kritischen Abschnitt ohne Lock aus:

    original = clipboard        # sichern
    clipboard = text            # Diktat setzen
    aktivieren + Cmd+V          # einfügen
    clipboard = original        # zurückschreiben

Liefen zwei davon gleichzeitig, verschränkten sich diese Abschnitte: Thread A
las als "Original" den Text von Thread B und schrieb ihn zurück, bevor die
Ziel-App Bs Einfügen überhaupt verarbeitet hatte. Im Terminal landete dann ein
altes Diktat. Besonders bei langen Nachrichten, weil deren Transkription lange
genug dauert, dass die nächste Aufnahme dazwischenkommt.

Zusätzlich hält die Queue die Reihenfolge: im Log drängelte sich ein kurzes
'[MUSIK]' vor ein 701-Zeichen-Diktat, weil es schneller transkribiert war.
Aufnahmen entstehen sequentiell (der Hotkey lässt sich nicht doppelt halten),
also ist FIFO genau die Reihenfolge, in der gesprochen wurde.
"""
import queue
import sys
import threading


def _log(msg):
    print(f"[VoiceShift/pipeline] {msg}", flush=True)
    sys.stderr.flush()


class SerialPipeline:
    """Eine FIFO-Queue mit genau EINEM Worker-Thread.

    handler(item) wird nie nebenläufig aufgerufen. Eine Ausnahme im Handler
    beendet den Worker nicht – ein gescheitertes Diktat darf nicht alle
    folgenden blockieren.
    """

    def __init__(self, handler, name="pipeline"):
        self._handler = handler
        self._name = name
        self._queue = queue.Queue()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def submit(self, item):
        self._queue.put(item)

    def _run(self):
        while True:
            item = self._queue.get()
            try:
                self._handler(item)
            except Exception as e:
                _log(f"Handler-Fehler ({e!r}) – Pipeline läuft weiter")
            finally:
                self._queue.task_done()
