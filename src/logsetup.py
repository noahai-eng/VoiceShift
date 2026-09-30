#!/usr/bin/env python3
"""
Dauerhafte Logs unter ~/.voiceshift/logs/voiceshift.log – mit Uhrzeit.

Die LaunchAgent-Logs in /tmp bleiben für `tail -f`, aber macOS leert /tmp bei
jedem Neustart. Als lange Diktate verloren gingen, war dadurch kein einziger
Fehlschlag mehr nachvollziehbar. Diese Datei überlebt Neustarts und wird bei
5 MB rotiert (eine alte Generation bleibt als .1 erhalten).

Enthält die diktierten Texte – liegt deshalb in einem nur für den Nutzer
lesbaren Ordner.
"""
import faulthandler
import os
import sys
import threading
import time

LOG_DIR  = os.path.expanduser("~/.voiceshift/logs")
LOG_FILE = os.path.join(LOG_DIR, "voiceshift.log")
MAX_BYTES = 5 * 1024 * 1024


def rotate_if_large(path: str, max_bytes: int = MAX_BYTES):
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except FileNotFoundError:
        pass


class TimestampTee:
    """Schreibt in mehrere Ziele und stellt jeder Zeile die Uhrzeit voran."""

    def __init__(self, *targets):
        self._targets = targets
        self._at_line_start = True
        self._lock = threading.Lock()

    def write(self, s):
        if not s:
            return 0
        with self._lock:
            out = []
            for part in s.splitlines(keepends=True):
                if self._at_line_start:
                    t = time.time()
                    out.append(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
                               + f".{int(t % 1 * 1000):03d} ")
                out.append(part)
                self._at_line_start = part.endswith("\n")
            text = "".join(out)
            for target in self._targets:
                try:
                    target.write(text)
                except Exception:
                    pass
        return len(s)

    def flush(self):
        for target in self._targets:
            try:
                target.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def install():
    """stdout/stderr zusätzlich in die dauerhafte Log-Datei leiten."""
    try:
        os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
        rotate_if_large(LOG_FILE)
        logfile = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    except OSError as e:
        print(f"[VoiceShift] Log-Datei nicht nutzbar: {e!r}", flush=True)
        return
    sys.stdout = TimestampTee(sys.__stdout__, logfile)
    sys.stderr = TimestampTee(sys.__stderr__, logfile)
    # Native Abstürze (Segfault in PyObjC/PortAudio) mit Python-Stacks loggen.
    faulthandler.enable(file=logfile, all_threads=True)
