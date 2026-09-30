#!/usr/bin/env python3
"""
Lebenszyklus der Aufnahme-Dateien in ~/.voiceshift/recordings/.

Gelöscht wird nur, was erledigt ist (eingefügt oder nachweislich leer). Was
bei einem Absturz liegen bleibt, findet pending() beim nächsten Start; was
schon bei der Transkription scheitert, wandert nach failed/ – dort wird es
nicht erneut versucht (sonst Endlos-Schleife bei einer kaputten Datei), bleibt
aber zum manuellen Retten erhalten.
"""
import glob
import os
import shutil

from recorder import meta_path, pcm_to_wav

FAILED_DIR = os.path.expanduser("~/.voiceshift/recordings/failed")


def _log(msg):
    print(f"[VoiceShift/recordings] {msg}", flush=True)


def pending(rec_dir: str) -> list[str]:
    """Unerledigte Aufnahmen (WAV-Pfade), älteste zuerst.

    Verwaiste .pcm-Dateien (Prozess starb mitten in der Aufnahme) werden
    dabei zu WAVs umgewandelt. Nur beim Start aufrufen – während einer
    laufenden Aufnahme wäre deren .pcm sonst mit betroffen.
    """
    if not os.path.isdir(rec_dir):
        return []
    for pcm in glob.glob(os.path.join(rec_dir, "*.pcm")):
        try:
            pcm_to_wav(pcm)
        except OSError as e:
            _log(f"verwaiste Aufnahme {pcm} nicht lesbar: {e!r}")
    # Dateinamen beginnen mit Datum+Uhrzeit → Sortierung = Aufnahmereihenfolge.
    return sorted(glob.glob(os.path.join(rec_dir, "*.wav")))


def discard(audio_path: str):
    """Erledigte Aufnahme samt Metadaten löschen."""
    for p in (audio_path, meta_path(audio_path)):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


def quarantine(audio_path: str, failed_dir: str = FAILED_DIR):
    """Gescheiterte Aufnahme aufheben, aber aus dem Nachhol-Ordner nehmen."""
    os.makedirs(failed_dir, exist_ok=True)
    for p in (audio_path, meta_path(audio_path)):
        if os.path.exists(p):
            shutil.move(p, os.path.join(failed_dir, os.path.basename(p)))
    _log(f"Aufnahme aufgehoben in {failed_dir}: {os.path.basename(audio_path)}")
