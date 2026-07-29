#!/bin/bash
# Triggert den macOS Mikrofon-Permission-Dialog für VoiceShift.app, indem es
# einen kurzen InputStream aus dem Vordergrund (Terminal) öffnet.
set -e
PY=/Applications/VoiceShift.app/Contents/MacOS/python
if [ ! -x "$PY" ]; then
  echo "FEHLER: $PY existiert nicht. Erst scripts/build_app_bundle.sh ausführen."
  exit 1
fi
echo "🎤  Fordere Mikrofon-Permission für VoiceShift.app an …"
echo "    Wenn ein Dialog erscheint: 'Erlauben' klicken."
echo "    Danach automatisch 5-Sekunden-Aufnahme-Test."
echo
"$PY" - <<'PY'
import sounddevice as sd, numpy as np
print("Öffne InputStream auf default device …", flush=True)
sr = 16000
rec = sd.rec(int(sr * 5), samplerate=sr, channels=1, dtype="int16")
print("Bitte 5 Sekunden in dein Mikrofon sprechen …", flush=True)
sd.wait()
peak = int(np.max(np.abs(rec))) if rec.size else 0
rms  = float(np.sqrt(np.mean(rec.astype(np.float32) ** 2)))
print(f"Aufnahme fertig. peak={peak}, rms={rms:.1f} (int16 max=32767)", flush=True)
if peak == 0:
    print("❌  Stille aufgezeichnet — Mikrofon-Permission wurde NICHT erteilt.", flush=True)
else:
    print("✅  Mikrofon-Permission ist aktiv. VoiceShift sollte jetzt funktionieren.", flush=True)
PY
