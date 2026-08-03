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
