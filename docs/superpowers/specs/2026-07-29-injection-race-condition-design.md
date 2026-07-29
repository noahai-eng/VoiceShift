# Injection Race Condition beheben

**Datum:** 2026-07-29
**Status:** Design freigegeben

## Problem

Bei Diktaten nach Ghostty landet wiederholt der falsche Text im Terminal: alter
Inhalt aus einem früheren Diktat statt des gerade Gesprochenen. Besonders
häufig bei langen Nachrichten.

### Ursache (belegt)

`menu_bar.py:245` startet pro Aufnahme einen eigenen Thread für
`_process` (Transkription + Injection). Es gibt keine Serialisierung. Der
Injector führt aber in `_activate_and_type` (`injector.py:173-197`) einen
kritischen Abschnitt ohne Lock aus:

```
original = clipboard        # sichern
clipboard = text            # Diktat setzen
activate(pid) + Cmd+V       # einfügen
clipboard = original        # zurückschreiben
```

Laufen zwei Injections gleichzeitig, verschränken sich diese Abschnitte:
Thread A liest als „Original" den Text von Thread B (oder ein früheres Diktat)
und schreibt ihn zurück, während die Ziel-App Bs Paste noch nicht gelesen hat.
Die App liest dann den zurückgeschriebenen alten Wert.

Aus `/tmp/voiceshift.out.log`, Zeilen 3753–3775 — zwei überlappende
`_process`-Läufe nach Ghostty:

```
_process: starte Transkription … tmpaifczr6h.wav   ← Aufnahme A läuft noch
_process: starte Transkription … tmpk0t5n7h8.wav   ← Aufnahme B startet parallel
_process: Transkription fertig, len=7: '[MUSIK]'   ← B zuerst fertig
_process: injiziere Text …
_process: Transkription fertig, len=701: 'zu schritt 1 …'
_process: injiziere Text …                          ← A injiziert mitten in B
Injection abgeschlossen (angekommen=True)
Injection abgeschlossen (angekommen=True)
```

**Warum gerade lange Nachrichten:** Whisper braucht für 900+ Zeichen mehrere
Sekunden. In dieser Zeit startet der Nutzer die nächste Aufnahme — die
Überlappung wird mit der Länge wahrscheinlicher.

**Warum gerade Ghostty:** In allen Ghostty-Fällen im Log steht
`AX: Feld nicht beschreibbar – aktiviere Zielfenster`. Bei Claude und Safari
greift oft der lautlose AX-Pfad ganz ohne Clipboard. Ghostty fällt **immer**
auf den Clipboard-Pfad zurück und ist der Race Condition damit voll ausgesetzt.

### Nebenbefund

`[MUSIK]` wurde 5× als echter Text injiziert — eine Whisper-Halluzination bei
nahezu stillen Aufnahmen.

### Nicht die Ursache

- Die Hintergrund-Fenster-Funktion an sich. Sie hat den fehlenden Lock nur
  sichtbar gemacht, nicht verursacht.
- Ghostty `clipboard-paste-protection` bei mehrzeiligem Text.
  `normalize_transcript` (`transcriber.py:11`) macht Transkripte bereits
  einzeilig; die Schutzabfrage greift gar nicht.

## Lösung

### Fix 1 — Serialisierte Verarbeitung (`menu_bar.py`)

Zweite FIFO-Queue mit genau einem Consumer-Thread ersetzt den Thread pro
Aufnahme. Der bestehende Recording-Worker bleibt unverändert, damit Audio-
Start/Stop weiter sofort reagieren, während transkribiert wird.

```
Hotkey → _cmd_queue → recording worker → _process_queue → process worker (einer)
                      (Audio start/stop)                   (Transkription + Injection)
```

Garantien:

- Nie zwei Injections gleichzeitig — der Clipboard-Abschnitt wird unteilbar.
- Reihenfolge bleibt die gesprochene (Aufnahmen sind von Natur aus sequentiell,
  der Hotkey lässt sich nicht doppelt halten → FIFO = Sprechreihenfolge).

Bewusst in Kauf genommen: eine lange Transkription verzögert die nächste.
Richtige Reihenfolge schlägt Tempo, und serialisiert werden muss ohnehin.

### Fix 2 — Clipboard nur bei Nachweis zurücksetzen (`injector.py:195-197`)

Heute wird auch bei `confirmed is None` („nicht prüfbar") das alte Clipboard
zurückgeschrieben. Dieser Fall trifft Ghostty systematisch: der Claude-Code-
Spinner tickt, `AXValue` ändert sich, `changed=True` → `None` → Restore nach
0,6 s, möglicherweise bevor Ghostty gelesen hat. Der Kommentar bei
`injector.py:240-254` beschreibt das Problem bereits, zieht aber die falsche
Konsequenz.

Neu: nur bei bewiesenem `True` zurücksetzen. Bei `None` und `False` bleibt das
Diktat im Clipboard.

Abwägung: schlimmstenfalls geht der vorherige Clipboard-Inhalt verloren. Nie
aber landet ein falscher alter Text im Zielfenster. Das ist die richtige
Richtung für den Fehlerfall.

### Fix 3 — Halluzinations-Filter (`transcriber.py`)

Transkript verwerfen, wenn es **ausschließlich** aus Klammer-Tags besteht
(`[MUSIK]`, `[BLANK_AUDIO]`, `(Applaus)` …).

Ausdrücklich **keine** Mindestlänge: im Log stehen `'Mach!'` (5 Zeichen) und
`'Äh...'` als echte Diktate. Eine Längenheuristik würde sie fressen.

Zusätzlich: bei `peak == 0` gar nicht erst transkribieren. Der Recorder misst
den Peak bereits (`recorder.py:96`), gibt ihn aber noch nicht nach außen.

### Fix 4 — Versionskontrolle

Erledigt: `git init` plus Initial-Commit des Stands vor den Fixes
(`1d7a932`). Danach ein Commit pro Fix, damit jeder einzeln zurückrollbar ist.

## Tests

- **Fix 1:** Test, der zwei Injections nebenläufig abfeuert und nachweist, dass
  sich ihre Clipboard-Abschnitte nicht verschränken (Injection B beginnt erst,
  nachdem A fertig ist).
- **Fix 2:** Tests für alle drei Rückgabefälle von `_paste_and_confirm` —
  `True` → Clipboard zurückgesetzt; `None` und `False` → Diktat bleibt liegen.
- **Fix 3:** Tabellentest mit den belegten Artefakten aus dem Log als
  Negativfälle und den echten Kurzdiktaten (`'Mach!'`, `'Äh...'`) als
  Positivfälle.

Basis ist `tests/test_injector.py` (436 Zeilen, bestehende Fakes für die
System-Aufrufe).

## Abgrenzung

Nicht Teil dieser Arbeit:

- Phase 2 / KI-Transformationen (`src/transformer.py`).
- Der leere Ordner `{src,assets,scripts}` im Projektwurzelverzeichnis —
  offensichtlich ein Shell-Unfall, aber Aufräumen ist eine eigene Entscheidung.
- Neuschrieb der App. Bewusst verworfen: die Ursache ist ein fehlender Lock,
  kein Architekturproblem, und die teuer erkauften macOS-Erkenntnisse stehen
  bereits als Doku in `injector.py:19-35`.
