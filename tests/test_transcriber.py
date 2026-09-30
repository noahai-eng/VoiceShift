#!/usr/bin/env python3
"""Tests für die Nachbearbeitung des Whisper-Outputs.

Whisper schreibt pro Segment eine Zeile. Diese Umbrüche sind ARTEFAKTE der
Segmentierung, kein gewolltes Format – sie landen mitten im Satz
("... Punkt 2.\\n wird es mehr Sinn machen ..."). Ungefiltert richten sie
Schaden an:
  * Ghostty (clipboard-paste-protection) zeigt bei mehrzeiligem Text einen
    Bestätigungsdialog → das Diktat kommt gar nicht erst an.
  * In Chat-Apps sendet ein Zeilenumbruch die Nachricht mitten im Satz ab.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from transcriber import is_hallucination, normalize_transcript  # noqa: E402


def test_segment_umbruch_wird_zu_leerzeichen():
    roh = "Das ist so, dass bei jeder Frage einfach nur immer in\nWissensdatenbank hineingeschaut wird."
    assert normalize_transcript(roh) == (
        "Das ist so, dass bei jeder Frage einfach nur immer in "
        "Wissensdatenbank hineingeschaut wird.")


def test_fuehrende_leerzeichen_der_segmente_verschwinden():
    """Whisper rückt Folgesegmente oft mit einem Leerzeichen ein."""
    assert normalize_transcript("Erster Teil.\n zweiter Teil.") == "Erster Teil. zweiter Teil."


def test_mehrere_umbrueche_und_leerzeilen():
    assert normalize_transcript("Eins.\n\nZwei.\n \nDrei.") == "Eins. Zwei. Drei."


def test_windows_umbrueche():
    assert normalize_transcript("Eins.\r\nZwei.") == "Eins. Zwei."


def test_einzeiliger_text_bleibt_unveraendert():
    assert normalize_transcript("Ein ganz normaler Satz.") == "Ein ganz normaler Satz."


def test_doppelte_leerzeichen_werden_zusammengefasst():
    assert normalize_transcript("Eins.  \n  Zwei.") == "Eins. Zwei."


def test_leerer_text():
    assert normalize_transcript("") == ""
    assert normalize_transcript("\n\n  \n") == ""


def test_umlaute_bleiben_erhalten():
    assert normalize_transcript("Grüße über Öl.\nWeiß größer.") == "Grüße über Öl. Weiß größer."


# ── Halluzinations-Filter ──────────────────────────────────────────────────
# Whisper erfindet bei (fast) stillen Aufnahmen Nichtsprach-Marker und schreibt
# sie in Klammern. Die landeten ungefiltert als echter Text im Zielfenster –
# '[MUSIK]' fünfmal in /tmp/voiceshift.out.log.
#
# Der Filter muss ENG sein: im selben Log stehen 'Mach!' und 'Äh...' als echte
# Diktate. Eine Mindestlänge würde sie fressen.

def test_musik_marker_ist_halluzination():
    """Der belegte Fall: 5x '[MUSIK]' im Log, als echter Text injiziert."""
    assert is_hallucination("[MUSIK]") is True


def test_weitere_klammer_marker():
    for text in ["[Musik]", "[BLANK_AUDIO]", "(Applaus)", "[ Musik ]",
                 "[MUSIK] [MUSIK]", "( Musik )"]:
        assert is_hallucination(text) is True, f"{text!r} muss verworfen werden"


def test_leerer_text_ist_halluzination():
    assert is_hallucination("") is True
    assert is_hallucination("   ") is True


def test_echte_kurzdiktate_kommen_durch():
    """Aus dem Log – echte, gewollte Diktate. Keine Längenheuristik!"""
    for text in ["Mach!", "Äh...", "Ja.", "Weiter", "Bei heute definitiv Entwurf A."]:
        assert is_hallucination(text) is False, f"{text!r} ist ein echtes Diktat"


def test_klammer_im_echten_satz_ist_keine_halluzination():
    """Nur wenn der GANZE Text aus Markern besteht, wird verworfen."""
    assert is_hallucination("Der Ton (Musik) war zu laut.") is False
    assert is_hallucination("[MUSIK] danach bitte weitermachen") is False


# ── Aufnahmelänge: Antippen verwerfen, Timeout mitwachsen lassen ─────────

import subprocess  # noqa: E402
import wave  # noqa: E402

import transcriber as transcriber_mod  # noqa: E402
from transcriber import Transcriber, whisper_timeout  # noqa: E402


def _wav(path, seconds):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * seconds))
    return str(path)


def test_versehentliches_antippen_geht_nicht_an_whisper(tmp_path, monkeypatch):
    """Whisper erfindet bei Beinahe-Stille echte Sätze ('Vielen Dank.'),
    die der Klammer-Filter nicht erkennt und die dann eingefügt würden."""
    def boom(*a, **kw):
        raise AssertionError("whisper darf für ein Antippen nicht laufen")
    monkeypatch.setattr(transcriber_mod.subprocess, "run", boom)

    assert Transcriber().transcribe(_wav(tmp_path / "a.wav", 0.2),
                                    delete_after=False) == ""


def test_timeout_waechst_mit_der_aufnahmelaenge(tmp_path, monkeypatch):
    """Fest 60 s: ein 10-Minuten-Diktat auf ausgelasteter GPU wäre verloren."""
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(transcriber_mod.subprocess, "run", fake_run)

    Transcriber().transcribe(_wav(tmp_path / "b.wav", 1.0), delete_after=False)
    assert seen["timeout"] == whisper_timeout(1.0)


def test_timeout_werte():
    assert whisper_timeout(1) >= 60                 # kurze Diktate wie bisher
    assert whisper_timeout(600) >= 600              # 10 min: mind. Echtzeit
