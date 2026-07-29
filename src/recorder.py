#!/usr/bin/env python3
"""
AudioRecorder – nimmt Mikrofon-Audio auf und speichert als WAV.
Beliebig viele Aufnahmen hintereinander möglich (Stream wird vor jeder
neuen Aufnahme sauber beendet, Frames werden zurückgesetzt).
"""
import threading
import tempfile
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000   # Whisper bevorzugt 16 kHz
CHANNELS    = 1
DTYPE       = "int16"


class AudioRecorder:
    def __init__(self):
        self._frames = []
        self._stream = None
        self._lock   = threading.Lock()

    def start(self):
        with self._lock:
            self._teardown_stream()
            self._frames = []
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
                print(
                    f"[VoiceShift/recorder] Stream-Open fehlgeschlagen ({e}) – "
                    f"PortAudio neu initialisieren und erneut versuchen.",
                    flush=True,
                )
                self._teardown_stream()
                sd._terminate()
                sd._initialize()
                self._open_stream()

    def _open_stream(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        self._frames.append(indata.copy())

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

    def stop(self) -> str:
        """Stoppt Aufnahme, schreibt WAV-Datei, gibt Pfad zurück."""
        with self._lock:
            self._teardown_stream()

            frames = self._frames
            self._frames = []   # Reset für nächste Aufnahme

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()

            audio = (np.concatenate(frames, axis=0)
                     if frames else np.array([], dtype=np.int16))
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)          # int16 = 2 Bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())

            # Diagnose: ist da überhaupt Audio drauf? Loggt nur bei Stille,
            # damit normale Aufnahmen kein Log-Spam erzeugen — peak=0 ist
            # meist ein Hinweis auf fehlende Mikrofon-Permission oder das
            # falsche default device.
            n = int(audio.size)
            peak = int(np.max(np.abs(audio))) if n else 0
            if peak == 0:
                print(
                    f"[VoiceShift/recorder] STILLE: {n / SAMPLE_RATE:.2f}s, peak=0. "
                    f"Mikrofon-Permission prüfen oder default device wechseln "
                    f"(aktuell: {sd.default.device}).",
                    flush=True,
                )
            return tmp.name
