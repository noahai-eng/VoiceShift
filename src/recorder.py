#!/usr/bin/env python3
"""
AudioRecorder – nimmt Mikrofon-Audio auf und speichert als WAV.
Beliebig viele Aufnahmen hintereinander möglich (Stream wird vor jeder
neuen Aufnahme sauber beendet).

GRUNDREGEL – ein Diktat geht nie verloren:
  Das Stoppen des PortAudio-Streams kann in einem CoreAudio-HAL-Deadlock
  hängen bleiben; der Watchdog muss die App dann neu starten. Früher lag die
  Aufnahme bis zum Stopp NUR im Arbeitsspeicher und war damit weg (live
  belegt am 2026-09-30: "WATCHDOG: recorder.stop() hängt seit 8s").
  Deshalb:
    * jeder Audio-Block wird sofort ungepuffert in eine .pcm-Datei geschrieben
      (liegt damit im Kernel-Cache und überlebt jeden Prozess-Tod),
    * finish() stellt die WAV fertig, OHNE CoreAudio anzufassen,
    * erst danach stoppt teardown() den Stream – die Stelle, die hängen kann.
"""
import json
import os
import threading
import time
import uuid
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000   # Whisper bevorzugt 16 kHz
CHANNELS    = 1
DTYPE       = "int16"
# Feste Blockgröße (100 ms). Ohne sie liefert PortAudio beim Umrechnen vom
# 48-kHz-Mikrofon auf 16 kHz winzige Blöcke von ~15 Samples – gemessen ~1060
# Python-Callbacks pro Sekunde, die alle um das GIL konkurrieren (u. a. mit
# dem Hotkey-Thread, dessen Loslassen sich dadurch spürbar verzögerte).
BLOCKSIZE   = 1600

REC_DIR = os.path.expanduser("~/.voiceshift/recordings")


def _log(msg):
    print(f"[VoiceShift/recorder] {msg}", flush=True)


def pcm_to_wav(pcm_path: str) -> str:
    """Rohes int16-PCM in eine WAV daneben umwandeln, .pcm löschen, WAV-Pfad liefern.

    Wird am Ende jeder Aufnahme benutzt und beim Start, um nach einem harten
    Absturz verwaiste Aufnahmen zu retten.
    """
    wav_path = pcm_path[: -len(".pcm")] + ".wav"
    with open(pcm_path, "rb") as f:
        data = f.read()
    data = data[: len(data) - len(data) % 2]   # halbes Sample beim Absturz abschneiden
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)          # int16 = 2 Bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(data)
    os.unlink(pcm_path)
    return wav_path


def meta_path(audio_path: str) -> str:
    return os.path.splitext(audio_path)[0] + ".json"


def read_meta(audio_path: str) -> dict:
    try:
        with open(meta_path(audio_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class AudioRecorder:
    def __init__(self, rec_dir: str = REC_DIR):
        self._rec_dir = rec_dir
        self._stream = None
        self._lock   = threading.Lock()   # serialisiert start/teardown
        # Schützt die Datei zwischen Audio-Callback und finish().
        self._sink_lock = threading.Lock()
        self._sink = None
        self._pcm_path = None
        self._peak = 0
        self._samples = 0

    def start(self, meta: dict | None = None):
        with self._lock:
            self._teardown_stream()
            os.makedirs(self._rec_dir, exist_ok=True)
            base = os.path.join(
                self._rec_dir,
                time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
            if meta:
                with open(base + ".json", "w", encoding="utf-8") as f:
                    json.dump(meta, f)
            with self._sink_lock:
                self._pcm_path = base + ".pcm"
                # buffering=0: jeder Block geht sofort per write() an den Kernel.
                self._sink = open(self._pcm_path, "wb", buffering=0)
                self._peak = 0
                self._samples = 0
            try:
                self._open_stream()
            except sd.PortAudioError as e:
                # PortAudio liest die Geräteliste nur einmal beim Initialisieren
                # (passiert beim Import von sounddevice). Ändert sich danach die
                # Gerätelandschaft – Bluetooth-Gerät, Kopfhörer, coreaudiod-Neustart –,
                # zeigen die gemerkten Device-IDs ins Leere ('!obj' = BadObject) und
                # JEDER weitere Stream-Open scheitert dauerhaft, auch mit frischem
                # InputStream: der kaputte Zustand liegt global in der Bibliothek.
                # Neu-Initialisieren baut die Geräteliste neu auf und heilt den Prozess.
                _log(f"Stream-Open fehlgeschlagen ({e}) – "
                     f"PortAudio neu initialisieren und erneut versuchen.")
                self._teardown_stream()
                sd._terminate()
                sd._initialize()
                self._open_stream()

    def _open_stream(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        with self._sink_lock:
            if self._sink is None:
                return   # Nachzügler nach finish() – gehört zu keiner Aufnahme
            self._sink.write(indata.tobytes())
            self._samples += len(indata)
            if len(indata):
                self._peak = max(self._peak, int(np.max(np.abs(indata))))

    def finish(self) -> str | None:
        """Beendet die Aufnahme und liefert die fertige WAV.

        Fasst CoreAudio NICHT an und kann daher nicht im HAL-Deadlock hängen.
        Den Stream stoppt anschließend teardown().
        """
        with self._sink_lock:
            sink, self._sink = self._sink, None
            pcm_path, peak, samples = self._pcm_path, self._peak, self._samples
            self._pcm_path = None
        if sink is None:
            return None
        sink.close()
        wav_path = pcm_to_wav(pcm_path)

        # Diagnose: ist da überhaupt Audio drauf? Loggt nur bei Stille,
        # damit normale Aufnahmen kein Log-Spam erzeugen — peak=0 ist
        # meist ein Hinweis auf fehlende Mikrofon-Permission oder das
        # falsche default device.
        if peak == 0:
            _log(f"STILLE: {samples / SAMPLE_RATE:.2f}s, peak=0. "
                 f"Mikrofon-Permission prüfen oder default device wechseln "
                 f"(aktuell: {sd.default.device}).")
        return wav_path

    def teardown(self):
        """Stoppt den Stream. KANN im CoreAudio-HAL-Deadlock hängen."""
        with self._lock:
            self._teardown_stream()

    def _teardown_stream(self):
        if self._stream is None:
            return
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
        self._stream = None
