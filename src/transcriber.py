#!/usr/bin/env python3
"""
Transcriber – ruft whisper.cpp CLI auf und gibt transkribierten Text zurück
"""
import re
import subprocess
import os
import wave

WHISPER_BIN = os.path.expanduser("~/.voiceshift/whisper.cpp/build/bin/whisper-cli")
MODEL_PATH  = os.path.expanduser("~/.voiceshift/models/ggml-small.bin")

# Kürzer ist ein versehentliches Antippen von Ctrl+Shift, kein Diktat. Whisper
# erfindet bei solchen Beinahe-Stille-Schnipseln gern echte Sätze
# ("Vielen Dank.", "Untertitel im Auftrag des ZDF"), die der Klammer-Filter
# unten nicht erkennt – sie würden eingefügt.
MIN_SECONDS = 0.4


def audio_seconds(path: str) -> float:
    try:
        with wave.open(path) as wf:
            return wf.getnframes() / float(wf.getframerate())
    except (OSError, wave.Error, ZeroDivisionError):
        return 0.0


def whisper_timeout(seconds: float) -> float:
    """Obergrenze für whisper-cli, wächst mit der Aufnahme.

    Gemessen (M1, Metal): ~12× schneller als Echtzeit, 5 min Audio in 25 s.
    Echtzeit + 30 s lässt reichlich Luft für eine ausgelastete GPU; früher
    fest 60 s, womit ein langes Diktat unter Last komplett verloren war.
    """
    return max(60.0, 30.0 + seconds)


# Nichtsprach-Marker, die Whisper bei (fast) stillen Aufnahmen erfindet:
# "[MUSIK]", "[BLANK_AUDIO]", "(Applaus)". Ein Text, der NUR aus solchen
# Markern besteht, ist eine Halluzination und darf nicht injiziert werden.
_MARKER = re.compile(r"[\[(][^\])]*[\])]")


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


def is_hallucination(text: str) -> bool:
    """True, wenn der Text nichts Diktiertes enthält und verworfen gehört.

    Whisper erfindet bei stillen oder fast stillen Aufnahmen Nichtsprach-Marker
    in Klammern. '[MUSIK]' landete so fünfmal als echter Text im Zielfenster
    (belegt in /tmp/voiceshift.out.log).

    BEWUSST eng gehalten: verworfen wird nur, was AUSSCHLIESSLICH aus solchen
    Markern besteht. Keine Mindestlänge – im selben Log stehen 'Mach!' und
    'Äh...' als echte, gewollte Diktate, die eine Längenheuristik fressen würde.
    """
    if not text or not text.strip():
        return True
    return not _MARKER.sub("", text).strip()


class Transcriber:
    def transcribe(self, audio_path: str, lang: str = "de", delete_after: bool = True) -> str:
        """
        Transkribiert eine WAV-Datei mit whisper.cpp.
        Gibt den erkannten Text zurück (leer-String wenn nichts erkannt).
        """
        seconds = audio_seconds(audio_path)
        if seconds < MIN_SECONDS:
            print(f"[VoiceShift/transcriber] {seconds:.2f}s – Antippen, kein Diktat "
                  f"→ verworfen", flush=True)
            if delete_after:
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass
            return ""

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

        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=whisper_timeout(seconds))
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

        if is_hallucination(text):
            if text:
                print(f"[VoiceShift/transcriber] Halluzination verworfen: {text!r}",
                      flush=True)
            return ""

        return text
