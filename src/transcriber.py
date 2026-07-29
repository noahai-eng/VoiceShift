#!/usr/bin/env python3
"""
Transcriber – ruft whisper.cpp CLI auf und gibt transkribierten Text zurück
"""
import subprocess
import os

WHISPER_BIN = os.path.expanduser("~/.voiceshift/whisper.cpp/build/bin/whisper-cli")
MODEL_PATH  = os.path.expanduser("~/.voiceshift/models/ggml-small.bin")


def normalize_transcript(text: str) -> str:
    """Whisper-Segmentumbrüche zu Leerzeichen machen.

    Whisper schreibt pro Segment eine Zeile; die Umbrüche fallen mitten in den
    Satz ("... Punkt 2.\\n wird es mehr Sinn machen ..."). Sie sind ein Artefakt
    der Segmentierung, kein diktiertes Format – und sie richten Schaden an:
      * Ghostty (clipboard-paste-protection=true, Standard) hält mehrzeiligen
        Text für unsicher und verlangt eine Bestätigung – das Diktat kommt gar
        nicht erst an.
      * In Chat-Apps sendet ein Zeilenumbruch die Nachricht mitten im Satz ab.
    """
    return " ".join(text.split())

class Transcriber:
    def transcribe(self, audio_path: str, lang: str = "de", delete_after: bool = True) -> str:
        """
        Transkribiert eine WAV-Datei mit whisper.cpp.
        Gibt den erkannten Text zurück (leer-String wenn nichts erkannt).
        """
        if not os.path.exists(WHISPER_BIN):
            raise FileNotFoundError(
                f"whisper-cli nicht gefunden:\n{WHISPER_BIN}\n\n"
                "Bitte scripts/setup.sh ausführen."
            )
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"Whisper-Modell nicht gefunden:\n{MODEL_PATH}\n\n"
                "Bitte scripts/setup.sh ausführen."
            )

        out_base = audio_path.replace(".wav", "")
        cmd = [
            WHISPER_BIN,
            "-m",  MODEL_PATH,
            "-f",  audio_path,
            "-l",  lang,
            # Kein Text-Kontext zwischen Segmenten: verhindert den Whisper-
            # Repetition-Loop (halluzinierte Satzwiederholungen bei Sprechpausen,
            # der Decoder beißt sich sonst am vorherigen Segment-Text fest).
            "-mc", "0",
            "--no-timestamps",
            "-otxt",            # Ausgabe als .txt-Datei
            "-of", out_base,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(
                f"[VoiceShift/transcriber] whisper-cli rc={result.returncode}\n"
                f"  stdout: {result.stdout[-400:]}\n  stderr: {result.stderr[-400:]}",
                flush=True,
            )

        txt_path = out_base + ".txt"
        text = ""
        if os.path.exists(txt_path):
            with open(txt_path, encoding="utf-8") as f:
                text = normalize_transcript(f.read())
            os.unlink(txt_path)
        else:
            print(f"[VoiceShift/transcriber] Keine Output-Datei: {txt_path}", flush=True)

        if delete_after:
            try:
                os.unlink(audio_path)
            except OSError:
                pass

        return text
