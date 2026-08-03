#!/usr/bin/env python3
"""Tests für TextInjector – Kernregel: NICHT fälschlich Erfolg melden.

Hintergrund (auf macOS 26 gemessen):
  Safari & Electron melden für kAXSelectedText settable=True und ihr
  SetAttributeValue gibt err=0 zurück – der Text wird aber still verworfen.
  Ohne Rück-Lesen hielt der Injector das für Erfolg, übersprang den Fallback
  und der Text war komplett verloren.

Die Fakes ersetzen nur die AX-/Aktivierungs-Nahtstellen, die Entscheidungs-
logik von inject() läuft unverändert.
"""
import os
import sys
import threading
import time

import ApplicationServices as AS

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from injector import TextInjector  # noqa: E402

SEL = AS.kAXSelectedTextAttribute
VAL = AS.kAXValueAttribute


class FakeField:
    """Textfeld mit einstellbarem (auch lügendem) Verhalten.

    accepts_selected_text=False + settable=True  = Safari/Electron-Verhalten:
    meldet beschreibbar, quittiert mit err=0, verwirft den Text.
    """

    def __init__(self, value="", sel_settable=True, accepts_selected_text=True,
                 val_settable=True, accepts_value=True, readable=True,
                 paste_placeholder=None):
        self.value                = value
        self.sel_settable         = sel_settable
        self.accepts_selected_text = accepts_selected_text
        self.val_settable         = val_settable
        self.accepts_value        = accepts_value
        self.readable             = readable
        self.paste_placeholder    = paste_placeholder

    def read(self):
        """Was ein AXValue-Lesezugriff zurückgibt (Spinner-Felder ticken hier)."""
        return self.value


class FakeInjector(TextInjector):
    def __init__(self, field=None, activate_ok=True, paste_lands=True):
        self.field       = field
        self.activate_ok = activate_ok
        self.paste_lands = paste_lands   # False = Paste-Dialog hängt (Ghostty)
        self.typed       = []
        self.activated   = []
        self.clipboard   = "ORIGINAL-CLIPBOARD"

    # ── AX-Nahtstellen ──
    def _find_text_field(self, pid):
        return self.field

    def _ax_is_settable(self, el, attr):
        return el.sel_settable if attr == SEL else el.val_settable

    def _ax_get_value(self, el):
        return el.read() if el.readable else None

    def _ax_set(self, el, attr, value):
        if attr == SEL:
            if el.accepts_selected_text:
                el.value += value
        elif attr == VAL:
            if el.accepts_value:
                el.value = value
        return 0        # meldet IMMER Erfolg – genau wie Safari es tut

    def _ax_set_cursor_end(self, el, end):
        pass

    # ── Aktivierung / Einfügen ──
    def _activate(self, pid, timeout=None):
        self.activated.append(pid)
        return self.activate_ok

    def _paste_frontmost(self, text):
        self.typed.append(text)
        # Cmd+V geht am AX-Layer vorbei: der Text landet im Feld, auch wenn
        # dessen AX-Attribute nicht beschreibbar sind.
        if self.field is not None and self.paste_lands:
            # paste_placeholder simuliert Apps, die langen Text NICHT im
            # Wortlaut anzeigen (Claude Code: "[Pasted text +N lines]").
            self.field.value += (self.field.paste_placeholder
                                 if self.field.paste_placeholder is not None
                                 else text)
        return True

    # ── Clipboard ──
    def _get_clipboard(self):
        return self.clipboard

    def _set_clipboard(self, text):
        self.clipboard = text


class SpinnerField(FakeField):
    """Terminal-TUI, deren Inhalt sich von ALLEIN ändert.

    An Ghostty + Claude Code gemessen: AXValue ist lesbar und enthält den
    sichtbaren Screen – inklusive "· Whisking… (5m 8s · ↓ 17.4k tokens)".
    Der Timer zählt hoch, also ändert sich der Wert im Sekundentakt, ohne dass
    irgendjemand etwas eingefügt hätte.
    """

    def __init__(self, tick_every=0.05, **kw):
        # Wie das echte Ghostty: lesbar, aber NICHT beschreibbar (gemessen
        # settable=False für AXValue und AXSelectedText).
        kw.setdefault("sel_settable", False)
        kw.setdefault("val_settable", False)
        super().__init__(**kw)
        self._ticks      = 0
        self._tick_every = tick_every
        self._last_tick  = time.monotonic()

    def read(self):
        now = time.monotonic()
        if now - self._last_tick >= self._tick_every:
            self._ticks    += 1
            self._last_tick = now
            self.value += f"|tick{self._ticks}"
        return self.value


class GhosttyInjector(FakeInjector):
    """Ghostty liest das Pasteboard ERST beim Verarbeiten des Cmd+V.

    Gemessen: der Text taucht ~0.5 s nachdem `osascript` zurückkam im Terminal
    auf. Wird das Clipboard vorher zurückgesetzt, fügt Ghostty den ALTEN Inhalt
    ein – deshalb hält dieser Fake fest, was zum Lesezeitpunkt im Clipboard lag.
    """

    def __init__(self, field, land_delay=0.35, **kw):
        super().__init__(field, **kw)
        self.land_delay      = land_delay
        self.landed_text     = None
        self.paste_at        = None
        self.clipboard_writes = []
        self._timer          = None

    def _paste_frontmost(self, text):
        self.typed.append(text)
        self.paste_at = time.monotonic()

        def land():
            # Ghostty liest JETZT – was auch immer gerade im Clipboard steht.
            self.landed_text = self.clipboard
            if self.field is not None and self.paste_lands:
                self.field.value += (self.field.paste_placeholder
                                     if self.field.paste_placeholder is not None
                                     else (self.landed_text or ""))

        self._timer = threading.Timer(self.land_delay, land)
        self._timer.start()
        return True

    def _set_clipboard(self, text):
        self.clipboard_writes.append((time.monotonic(), text))
        self.clipboard = text

    def wait(self):
        if self._timer is not None:
            self._timer.join()

    def restored_at(self, original):
        """Zeitpunkt, zu dem das ALTE Clipboard zurückgeschrieben wurde."""
        for t, text in self.clipboard_writes:
            if self.paste_at is not None and t >= self.paste_at and text == original:
                return t
        return None


def only_selected_text_works(**kw):
    return FakeField(accepts_selected_text=True, accepts_value=False, **kw)


def only_value_works(**kw):
    """Safari: SelectedText wird still verworfen, AXValue klappt."""
    return FakeField(accepts_selected_text=False, accepts_value=True, **kw)


def nothing_works(**kw):
    return FakeField(accepts_selected_text=False, accepts_value=False, **kw)


# ── Stufe 1: AXSelectedText (cursor-genau) ─────────────────────────────────

def test_selected_text_erfolg_kein_fokuswechsel():
    inj = FakeInjector(only_selected_text_works())
    assert inj.inject("Hallo", target_pid=42) is True

    assert inj.field.value == "Hallo"
    assert inj.activated == [], "Fokus darf NICHT geklaut werden, wenn AX klappt"
    assert inj.typed == []


def test_feld_mit_bestehendem_inhalt():
    inj = FakeInjector(only_selected_text_works(value="Bereits da. "))
    inj.inject("Neu", target_pid=42)

    assert inj.field.value == "Bereits da. Neu"
    assert inj.activated == []


def test_gleicher_text_zweimal():
    """Zweimal derselbe Text darf nicht als 'stand schon da' durchfallen."""
    inj = FakeInjector(only_selected_text_works(value="Hallo"))
    inj.inject("Hallo", target_pid=42)

    assert inj.field.value == "HalloHallo"
    assert inj.activated == []


# ── Stufe 2: AXValue (Safari/WebKit) ───────────────────────────────────────

def test_stiller_selected_text_fehlschlag_nutzt_axvalue():
    """DER Hauptbug: Schreiben meldet Erfolg, Text ist weg -> Stufe 2 rettet.

    Wichtig: immer noch OHNE Fokuswechsel – genau das Feature, das der
    Nutzer wollte.
    """
    inj = FakeInjector(only_value_works())
    assert inj.inject("Hallo", target_pid=42) is True

    assert inj.field.value == "Hallo"
    assert inj.activated == [], "Stufe 2 darf den Fokus NICHT klauen"
    assert inj.typed == []


def test_axvalue_haengt_an_bestehenden_text_an():
    inj = FakeInjector(only_value_works(value="Schon da. "))
    inj.inject("Neu", target_pid=42)

    assert inj.field.value == "Schon da. Neu"
    assert inj.activated == []


# ── Stufe 3: Aktivieren + Cmd+V ────────────────────────────────────────────

def test_beide_ax_wege_scheitern_faellt_zurueck():
    """Ghostty/Electron: nichts ist beschreibbar -> aktivieren und einfügen."""
    inj = FakeInjector(nothing_works())
    inj.inject("Hallo", target_pid=42)

    assert inj.activated == [42]
    assert inj.typed == ["Hallo"]


def test_nicht_beschreibbares_feld_faellt_zurueck():
    inj = FakeInjector(FakeField(sel_settable=False, val_settable=False))
    inj.inject("Hallo", target_pid=42)

    assert inj.activated == [42]
    assert inj.typed == ["Hallo"]


def test_kein_feld_gefunden_faellt_zurueck():
    inj = FakeInjector(field=None)
    inj.inject("Hallo", target_pid=42)

    assert inj.activated == [42]
    assert inj.typed == ["Hallo"]


def test_unlesbares_feld_schreibt_nicht_und_faellt_zurueck():
    """Wert nicht rücklesbar -> Erfolg unbeweisbar -> NICHT schreiben.

    Sonst stünde der Text am Ende doppelt im Feld (einmal per AX, einmal
    per Fallback).
    """
    # paste_lands=False: so schreibt NUR die AX-Schicht ins Feld – was hier
    # gerade nicht passieren darf.
    field = FakeField(readable=False)
    inj = FakeInjector(field, paste_lands=False)
    inj.inject("Hallo", target_pid=42)

    assert field.value == "", "AX darf gar nicht erst geschrieben haben"
    assert inj.activated == [42]
    assert inj.typed == ["Hallo"]


def test_ohne_aktivierung_wird_nicht_getippt():
    """Aktivierung fehlgeschlagen -> NICHT ins falsche Fenster schreiben.

    Blind einzufügen würde den Text in das Fenster kippen, in dem der Nutzer
    gerade arbeitet – schlimmer als gar nichts.
    """
    inj = FakeInjector(nothing_works(), activate_ok=False)
    assert inj.inject("Geheim", target_pid=42) is False

    assert inj.typed == [], "Darf NICHT ins falsche Fenster einfügen"
    assert inj.clipboard == "Geheim", "Text muss als Rettung im Clipboard bleiben"


def test_clipboard_wird_wiederhergestellt():
    inj = FakeInjector(nothing_works())
    inj.inject("Hallo", target_pid=42)

    assert inj.typed == ["Hallo"]
    assert inj.clipboard == "ORIGINAL-CLIPBOARD"


# ── Sonderfälle ────────────────────────────────────────────────────────────

def test_ohne_ziel_pid_ins_vorderste_fenster():
    inj = FakeInjector(only_selected_text_works())
    inj.inject("Hallo", target_pid=None)

    assert inj.typed == ["Hallo"]
    assert inj.activated == []


def test_leerer_text_macht_nichts():
    inj = FakeInjector(only_selected_text_works())
    assert inj.inject("", target_pid=42) is False
    assert inj.inject("   ", target_pid=42) is False

    assert inj.typed == []
    assert inj.activated == []


def test_unbestaetigter_paste_behaelt_text_im_clipboard():
    """Ghostty-Paste-Dialog: Einfügen steht noch aus.

    Das alte Clipboard jetzt zurückzuschreiben würde dazu führen, dass beim
    späteren Bestätigen der ALTE Inhalt landet – genau der Fehler, bei dem
    plötzlich ein früheres Diktat im Fenster stand.
    """
    inj = FakeInjector(nothing_works(), paste_lands=False)
    assert inj.inject("Neuer Text", target_pid=42) is False

    assert inj.typed == ["Neuer Text"], "Cmd+V wurde geschickt"
    assert inj.clipboard == "Neuer Text", "Clipboard darf NICHT zurückgesetzt werden"


def test_nicht_pruefbares_feld_stellt_clipboard_wieder_her():
    """Electron: Feld nicht lesbar -> Erfolg annehmen, Clipboard aufräumen."""
    inj = FakeInjector(FakeField(readable=False), paste_lands=False)
    assert inj.inject("Hallo", target_pid=42) is True

    assert inj.typed == ["Hallo"]
    assert inj.clipboard == "ORIGINAL-CLIPBOARD"


def test_paste_bestaetigt_auch_ohne_wortlaut():
    """Claude Code zeigt langen Text als "[Pasted text +N lines]".

    Der Wortlaut steht nie im Feld – aber das Feld ÄNDERT sich. Das reicht als
    Nachweis, sonst meldet jedes lange Diktat fälschlich "nicht angekommen" und
    das Clipboard bliebe unnötig überschrieben.
    """
    field = nothing_works(paste_placeholder="[Pasted text +12 lines]")
    inj = FakeInjector(field)
    assert inj.inject("Ein sehr langer diktierter Absatz ...", target_pid=42) is True

    assert inj.typed == ["Ein sehr langer diktierter Absatz ..."]
    assert inj.clipboard == "ORIGINAL-CLIPBOARD", "Clipboard wieder sauber"


def test_gar_keine_reaktion_bleibt_im_clipboard():
    """Feld reagiert NICHT (Paste-Dialog hängt) -> Text als Rettung behalten."""
    field = nothing_works()
    inj = FakeInjector(field, paste_lands=False)
    assert inj.inject("Wichtiger Text", target_pid=42) is False

    assert inj.clipboard == "Wichtiger Text"


# ── Ghostty: lesbar, nicht beschreibbar, liest das Pasteboard verzögert ─────
# Ghostty fällt zwischen die beiden Fälle, für die der Injector gebaut war:
#   * Electron   – AXValue NICHT lesbar -> _paste_and_confirm gibt None zurück
#                  und gönnt der App vorher _RESTORE_SETTLE.
#   * Cocoa-Apps – AX beschreibbar, Stufe 1/2 greift, Clipboard irrelevant.
# Ghostty ist lesbar UND unbeschreibbar: es braucht die Schonfrist, bekam sie
# aber als einzige App-Klasse nicht.

def test_ghostty_clipboard_erst_zurueck_wenn_der_text_gelesen_wurde():
    """DER Ghostty-Bug: Clipboard wird zurückgesetzt, bevor Ghostty liest.

    Der Confirm-Zweig `now != before` feuerte auf den TUI-Spinner statt auf das
    Einfügen, meldete "bestätigt" und der Aufrufer schrieb sofort das alte
    Clipboard zurück. Ghostty las danach – und fügte den ALTEN Text ein.
    """
    field = SpinnerField(value="prompt$ ")
    inj = GhosttyInjector(field, land_delay=0.35)
    inj.inject("Mein Diktat", target_pid=42)
    inj.wait()

    assert inj.landed_text == "Mein Diktat", (
        f"Ghostty hat {inj.landed_text!r} eingefügt statt des Diktats – "
        f"das Clipboard wurde zurückgesetzt, bevor Ghostty gelesen hat")


def test_spinner_verkuerzt_die_schonfrist_nicht():
    """Die Eigenbewegung der TUI darf die Wartezeit nicht abkürzen.

    Gemessen: AXValue ändert sich in Ghostty+Claude Code auch ohne jeden Paste.
    Ein solcher Tick ist KEIN Nachweis und darf das Zurücksetzen des Clipboards
    nicht auslösen.
    """
    field = SpinnerField(value="prompt$ ")
    inj = GhosttyInjector(field, land_delay=0.30)
    inj.inject("Diktierter Satz", target_pid=42)
    inj.wait()

    restored = inj.restored_at("ORIGINAL-CLIPBOARD")
    assert restored is not None, "Clipboard muss am Ende wiederhergestellt werden"
    assert restored >= inj.paste_at + inj.land_delay, (
        "Clipboard wurde nach %.3fs zurückgesetzt, Ghostty liest aber erst "
        "nach %.3fs" % (restored - inj.paste_at, inj.land_delay))


def test_fremde_clipboard_aenderung_wird_nicht_ueberschrieben():
    """Kopiert der Nutzer WÄHREND der Injection, gehört ihm das Clipboard.

    Der Injector sichert das alte Clipboard und schreibt es nach dem Einfügen
    zurück. Hat sich in der Zwischenzeit jemand anders eingetragen – typisch:
    der Nutzer kopiert etwas, während das Diktat noch verarbeitet wird –, dann
    ist der gesicherte Stand veraltet und darf die frische Kopie nicht killen.
    """
    class CopyDuringInjection(FakeInjector):
        def _paste_frontmost(self, text):
            ok = super()._paste_frontmost(text)
            # Der Nutzer drückt jetzt Cmd+C auf etwas anderem.
            self.clipboard = "FRISCH-KOPIERT"
            return ok

    inj = CopyDuringInjection(FakeField(readable=False))
    inj.inject("Mein Diktat", target_pid=42)

    assert inj.clipboard == "FRISCH-KOPIERT", (
        "Der Injector hat die frische Kopie des Nutzers mit dem veralteten "
        "gesicherten Stand überschrieben")


def test_fremde_clipboard_aenderung_auch_ohne_ziel_pid_geschuetzt():
    """Gleiche Regel im Fallback-Pfad (kein Zielfenster bekannt)."""
    class CopyDuringInjection(FakeInjector):
        def _paste_frontmost(self, text):
            ok = super()._paste_frontmost(text)
            self.clipboard = "FRISCH-KOPIERT"
            return ok

    inj = CopyDuringInjection(FakeField())
    inj.inject("Mein Diktat")          # ohne target_pid

    assert inj.clipboard == "FRISCH-KOPIERT"


def test_ghostty_mit_platzhalter_meldet_nicht_faelschlich_fehler():
    """Claude Code in Ghostty zeigt langen Text als "[Pasted text +N lines]".

    Der Wortlaut steht dann nie im AXValue. Zusammen mit dem tickenden Spinner
    darf daraus kein "nicht eingefügt" werden – der Text IST angekommen.
    """
    field = SpinnerField(value="prompt$ ",
                         paste_placeholder="[Pasted text +12 lines]")
    inj = GhosttyInjector(field, land_delay=0.20)
    assert inj.inject("Ein sehr langer diktierter Absatz ...", target_pid=42) is True
    inj.wait()

    assert inj.clipboard == "ORIGINAL-CLIPBOARD", "Clipboard wieder sauber"
