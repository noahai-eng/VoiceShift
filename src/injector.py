#!/usr/bin/env python3
"""
TextInjector – fügt Text in das beim Aufnahme-Start gemerkte Zielfenster ein.

Ziel: Der Nutzer spricht in Fenster A, lässt los und darf sofort woanders
hinklicken – der Text landet trotzdem in A (nie im falschen Fenster, nie verloren).

Drei Stufen, in dieser Reihenfolge:
  1. AXSelectedText  – schreibt am Cursor ins fokussierte Feld, ohne Fokuswechsel.
     Cursor-genau. Klappt bei nativen Cocoa-Feldern (TextEdit, Mail, Notizen).
  2. AXValue         – hängt den Text an den Feldinhalt an, ebenfalls ohne
     Fokuswechsel. Nötig für Safari/WebKit: dort wird ein AXSelectedText-Write
     still verworfen, ein AXValue-Write aber ausgeführt.
  3. Aktivieren + Cmd+V – für alles, was sich im Hintergrund nicht beschreiben
     lässt (Ghostty, Electron-Apps wie Claude/VS Code). Holt das gemerkte
     Zielfenster nach vorne; der Nutzer landet danach dort.
Schlägt auch das fehl, bleibt der Text im Clipboard – er geht nie verloren.

GRUNDREGEL – auf macOS 26 gemessen, das System meldet hier LEISE Falsches:
  * Safari/Electron melden für kAXSelectedText settable=True und ihr
    SetAttributeValue gibt err=0 zurück, verwerfen den Text aber still. Ohne
    Rück-Lesen hielt der Injector das für Erfolg, der Fallback lief nie und der
    Text war ersatzlos weg – der Hauptgrund, warum das Feature nicht funktionierte.
  * NSRunningApplication.activate…() gibt True zurück, ohne dass die App
    zwingend nach vorne kommt.
Deshalb wird JEDER Schritt zurückgelesen, statt Rückgabewerten zu glauben.

ZWEITE GRUNDREGEL – das Clipboard erst nach _RESTORE_SETTLE zurücksetzen:
  Das Ende von osascript heißt NICHT, dass die Ziel-App das Pasteboard schon
  gelesen hat. An Ghostty gemessen: der Text erscheint ~0.54 s später. Und
  "das Feld sieht jetzt anders aus" ist in einer Terminal-TUI kein Nachweis –
  dort tickt ein Spinner im Sekundentakt vor sich hin. Beides zusammen ließ
  Ghostty scheinbar zufällig scheitern: die Bestätigung feuerte auf den
  Spinner, das Clipboard war zurückgesetzt, bevor Ghostty las, und im Terminal
  landete der ALTE Inhalt.
"""
import subprocess
import time

import AppKit
import ApplicationServices as AS


def _log(msg):
    print(f"[VoiceShift/injector] {msg}", flush=True)


class TextInjector:
    # Wie lange auf "Zielfenster ist wirklich vorne" gewartet wird.
    _ACTIVATE_TIMEOUT = 3.0
    # Danach zusätzlich auf den Feld-Fokus warten (gemessen: bis ~0.26 s bei
    # Safari). Läuft die Frist ab, wird trotzdem eingefügt – manche Apps melden
    # nie ein fokussiertes AX-Element, funktionieren aber einwandfrei.
    _FOCUS_GRACE = 0.6
    # Kurze Beruhigung, bevor Cmd+V rausgeht bzw. das Clipboard zurückgesetzt wird.
    _PASTE_SETTLE   = 0.12
    # Mindestfrist zwischen Cmd+V und dem Zurücksetzen des Clipboards. Die
    # Ziel-App liest das Pasteboard erst, wenn sie das Tastenereignis abarbeitet
    # – nicht wenn osascript zurückkommt. An Ghostty gemessen: der Text steht
    # ~0.54 s nach dem Ende von osascript im Terminal. Wird vorher
    # zurückgesetzt, fügt die App den ALTEN Inhalt ein.
    _RESTORE_SETTLE = 0.60
    # So lange wird auf den Nachweis gewartet, dass der Text im Ziel steht.
    _PASTE_CONFIRM_TIMEOUT = 2.0
    # Baumsuche nach dem fokussierten Feld (nötig, weil Hintergrund-Safari kein
    # AXFocusedUIElement liefert). Gedeckelt, damit große Bäume nicht bremsen.
    _TREE_MAX_DEPTH = 14
    _TREE_BUDGET    = 1.5

    def inject(self, text: str, target_pid: int | None = None) -> bool:
        """Fügt Text ins gemerkte Zielfenster ein. True = nachweislich angekommen."""
        if not text or not text.strip():
            return False
        if target_pid:
            # 1./2. Bester Fall: still im Hintergrund ins Feld schreiben.
            if self._inject_ax(text, target_pid):
                return True
            # 3. Sonst: Zielfenster nach vorne holen und einfügen.
            return self._activate_and_type(text, target_pid)
        # Kein Ziel bekannt → altes Verhalten (vorderstes Fenster).
        return self._paste_with_clipboard(text)

    # ── 1)+2) Hintergrund-Einfügen über Accessibility (kein Fokuswechsel) ───
    def _inject_ax(self, text: str, pid: int) -> bool:
        """Schreibt Text ins fokussierte Feld der Ziel-App. True bei Erfolg."""
        try:
            field = self._find_text_field(pid)
            if field is None:
                _log(f"AX: kein fokussiertes Feld in pid={pid} – aktiviere Zielfenster")
                return False
            if self._try_selected_text(field, text):
                return True
            if self._try_value_append(field, text):
                return True
            _log(f"AX: Feld in pid={pid} nicht beschreibbar (Text kam nicht an) "
                 f"– aktiviere Zielfenster")
            return False
        except Exception as e:
            _log(f"AX-Ausnahme ({e!r}) – aktiviere Zielfenster")
            return False

    def _try_selected_text(self, field, text: str) -> bool:
        """Am Cursor einfügen (bevorzugt: erhält die Cursor-Position)."""
        if not self._ax_is_settable(field, AS.kAXSelectedTextAttribute):
            return False
        before = self._ax_get_value(field)
        if before is None:
            # Erfolg wäre unbeweisbar. Nicht schreiben – sonst landet der Text
            # womöglich doppelt (einmal hier, einmal im Fallback).
            return False
        if self._ax_set(field, AS.kAXSelectedTextAttribute, text) != 0:
            return False
        after = self._ax_get_value(field)
        return after is not None and after != before and text in after

    def _try_value_append(self, field, text: str) -> bool:
        """Gesamtwert neu setzen (Safari/WebKit ignoriert AXSelectedText).

        Hängt hinten an, statt am Cursor einzufügen – die Cursor-Position ist
        über AXValue nicht zuverlässig zu erhalten. Für Diktat ist das der
        normale Fall (Text wächst am Ende).
        """
        if not self._ax_is_settable(field, AS.kAXValueAttribute):
            return False
        before = self._ax_get_value(field)
        if before is None:
            return False
        if self._ax_set(field, AS.kAXValueAttribute, before + text) != 0:
            return False
        after = self._ax_get_value(field)
        if after is None or after == before or text not in after:
            return False
        # Cursor hinter den eingefügten Text setzen, damit der Nutzer
        # direkt weiterschreiben kann.
        try:
            self._ax_set_cursor_end(field, len(after))
        except Exception:
            pass
        return True

    def _find_text_field(self, pid: int):
        """Fokussiertes Feld der Ziel-App – auch wenn sie im Hintergrund liegt.

        Hintergrund-Safari liefert KEIN AXFocusedUIElement (gemessen), das
        fokussierte Feld steckt aber im AX-Baum und ist dort beschreibbar.
        """
        direct = self._ax_focused_element(pid)
        if direct is not None:
            return direct
        return self._find_focused_in_tree(pid)

    def _find_focused_in_tree(self, pid: int):
        root = AS.AXUIElementCreateApplication(pid)
        deadline = time.time() + self._TREE_BUDGET

        def walk(el, depth):
            if depth > self._TREE_MAX_DEPTH or time.time() > deadline:
                return None
            try:
                if self._ax_get(el, "AXFocused") is True:
                    return el
                for child in (self._ax_get(el, AS.kAXChildrenAttribute) or []):
                    found = walk(child, depth + 1)
                    if found is not None:
                        return found
            except Exception:
                pass
            return None

        return walk(root, 0)

    # ── 3) Zielfenster aktivieren und einfügen ─────────────────────────────
    def _activate_and_type(self, text: str, pid: int) -> bool:
        """Holt die Ziel-App (per PID) nach vorne und fügt den Text per Cmd+V ein."""
        original = self._get_clipboard()
        self._set_clipboard(text)

        if not self._activate(pid):
            # Bewusst NICHT blind einfügen: das Zielfenster ist nicht vorne, der
            # Text würde in das Fenster gehen, in dem der Nutzer gerade arbeitet.
            # Clipboard behält den Text – der Nutzer kann ihn mit Cmd+V retten.
            _log(f"Zielfenster pid={pid} kam nicht nach vorne – NICHT eingefügt. "
                 f"Text liegt im Clipboard (Cmd+V).")
            return False

        confirmed = self._paste_and_confirm(pid, text)
        if confirmed is False:
            # Einfügen hat NICHT stattgefunden – typisch: Ghostty fragt bei
            # mehrzeiligem Text erst nach (clipboard-paste-protection). Das
            # Clipboard jetzt zurückzusetzen würde bedeuten, dass beim späteren
            # Bestätigen der ALTE Inhalt landet (z. B. ein früheres Diktat).
            _log(f"Einfügen in pid={pid} nicht bestätigt – Text bleibt im "
                 f"Clipboard (evtl. wartet ein Paste-Dialog).")
            return False
        # confirmed True (nachgewiesen) oder None (nicht prüfbar) → sicher zurück.
        self._set_clipboard(original)
        return True

    def _paste_and_confirm(self, pid: int, text: str):
        """Cmd+V senden und prüfen, ob es angekommen ist.

        Rückgabe: True = nachgewiesen, False = nachweislich NICHT angekommen,
        None = nicht prüfbar (Feld nicht lesbar).

        Der Unterschied ist wichtig fürs Clipboard: solange ein Paste noch
        aussteht (Ghostty zeigt bei mehrzeiligem Text erst einen Bestätigungs-
        dialog), darf der alte Inhalt NICHT zurückgeschrieben werden – sonst
        fügt die App später den falschen, älteren Text ein.
        """
        field = self._find_text_field(pid)
        before = self._ax_get_value(field) if field is not None else None

        if not self._paste_frontmost(text):
            return False
        # Ab HIER läuft die Schonfrist: osascript ist zurück, die Ziel-App hat
        # das Cmd+V aber noch nicht zwingend verarbeitet.
        pasted_at = time.time()

        if before is None:
            # Nicht prüfbar (z. B. Electron) – der Ziel-App Zeit zum Lesen geben.
            self._settle_since(pasted_at)
            return None

        # Whitespace beidseitig entfernen: Terminals brechen lange Zeilen um,
        # sonst fände man den eingefügten Text nie wieder.
        needle = "".join(text.split())[:60]
        changed = False
        deadline = pasted_at + self._PASTE_CONFIRM_TIMEOUT
        while time.time() < deadline:
            now = self._ax_get_value(self._find_text_field(pid))
            if now is not None:
                if "".join(now.split()).find(needle) >= 0:
                    # Einziger echter Nachweis: unser Wortlaut steht im Ziel.
                    self._settle_since(pasted_at)
                    return True
                if now != before:
                    changed = True
            time.sleep(0.05)

        if changed:
            # Das Ziel hat sich verändert, aber nicht nachweisbar durch UNS.
            # Beides ist hier möglich und nicht unterscheidbar:
            #   * die App zeigt langen Text nicht im Wortlaut (Claude Code macht
            #     daraus "[Pasted text +N lines]"),
            #   * oder eine Terminal-TUI hat nur ihren eigenen Spinner/Timer
            #     neu gezeichnet (an Ghostty gemessen: "· Whisking… (5m 8s)"
            #     zählt hoch, AXValue ändert sich ohne jedes Zutun).
            # Früher galt das sofort als Erfolg – der Spinner beendete die
            # Bestätigung nach Millisekunden und das Clipboard war zurück-
            # gesetzt, bevor Ghostty gelesen hatte. Jetzt: unentschieden
            # melden, aber erst nach abgelaufener Schonfrist.
            self._settle_since(pasted_at)
            return None
        return False

    def _settle_since(self, pasted_at: float):
        """Wartet, bis seit dem Cmd+V _RESTORE_SETTLE vergangen ist.

        Der Aufrufer setzt direkt danach das Clipboard zurück. Ohne diese Frist
        liest die Ziel-App womöglich erst danach – und fügt den alten Inhalt
        ein. Bisher bekam nur der unlesbare Fall (Electron) sie; Ghostty ist
        lesbar und lief als einzige App-Klasse ungebremst durch.
        """
        rest = self._RESTORE_SETTLE - (time.time() - pasted_at)
        if rest > 0:
            time.sleep(rest)

    def _paste_with_clipboard(self, text: str) -> bool:
        """Einfügen ins gerade vorderste Fenster (kein Ziel bekannt)."""
        original = self._get_clipboard()
        self._set_clipboard(text)
        ok = self._paste_frontmost(text)
        time.sleep(self._RESTORE_SETTLE)
        self._set_clipboard(original)
        return ok

    def _activate(self, pid: int, timeout: float | None = None) -> bool:
        """Holt pid nach vorne und WARTET, bis das nachweislich passiert ist."""
        timeout = self._ACTIVATE_TIMEOUT if timeout is None else timeout
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None or app.isTerminated():
            _log(f"Ziel-App pid={pid} existiert nicht mehr")
            return False

        # Rückgabewert ist wertlos (True, auch wenn nichts passiert) → messen.
        app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_frontmost(pid):
                break
            time.sleep(0.02)
        else:
            return False

        # Fenster ist vorne – jetzt noch dem Textfeld Zeit zum Fokussieren geben.
        grace = time.time() + self._FOCUS_GRACE
        while time.time() < grace:
            if self._ax_focused_element(pid) is not None:
                break
            time.sleep(0.02)
        time.sleep(self._PASTE_SETTLE)
        return True

    # ── Systemaufrufe (in Tests überschrieben) ─────────────────────────────
    def _is_frontmost(self, pid: int) -> bool:
        """Ist pid gerade die vorderste App?

        Wird direkt am AX-Element der Ziel-App abgefragt. NSWorkspace.
        frontmostApplication() ist hier NICHT brauchbar: in einem Thread ohne
        Run-Loop – und _process läuft in genau so einem – liefert es dauerhaft
        veraltete Werte (gemessen: bleibt auf der zuerst gesehenen App stehen).
        """
        el = AS.AXUIElementCreateApplication(pid)
        err, val = AS.AXUIElementCopyAttributeValue(el, AS.kAXFrontmostAttribute, None)
        return err == 0 and bool(val)

    def _ax_focused_element(self, pid: int):
        app = AS.AXUIElementCreateApplication(pid)
        err, focused = AS.AXUIElementCopyAttributeValue(
            app, AS.kAXFocusedUIElementAttribute, None
        )
        return focused if err == 0 else None

    def _ax_get(self, el, attr):
        err, val = AS.AXUIElementCopyAttributeValue(el, attr, None)
        return val if err == 0 else None

    def _ax_get_value(self, el):
        val = self._ax_get(el, AS.kAXValueAttribute)
        return val if isinstance(val, str) else None

    def _ax_is_settable(self, el, attr) -> bool:
        err, settable = AS.AXUIElementIsAttributeSettable(el, attr, None)
        return err == 0 and bool(settable)

    def _ax_set(self, el, attr, value) -> int:
        return AS.AXUIElementSetAttributeValue(el, attr, value)

    def _ax_set_cursor_end(self, el, end: int):
        rng = AS.AXValueCreate(AS.kAXValueCFRangeType, (end, 0))
        if rng is not None:
            self._ax_set(el, AS.kAXSelectedTextRangeAttribute, rng)

    def _get_clipboard(self):
        pb = AppKit.NSPasteboard.generalPasteboard()
        return pb.stringForType_(AppKit.NSPasteboardTypeString)

    def _set_clipboard(self, text):
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        if text is not None:
            pb.setString_forType_(text, AppKit.NSPasteboardTypeString)

    def _paste_frontmost(self, text: str) -> bool:
        """Cmd+V ins vorderste Fenster.

        Bewusst Einfügen statt `keystroke <text>`: keystroke tippt Zeichen für
        Zeichen (verschluckt bei langen Diktaten Zeichen – gemessen 215 von 216)
        und schickt ein \\n als RETURN, was in Chat-Apps die Nachricht mitten im
        Satz absendet. Cmd+V überträgt den Text in einem Stück und wörtlich.
        """
        script = 'tell application "System Events" to keystroke "v" using command down'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            _log(f"osascript rc={result.returncode} stderr={result.stderr.strip()!r} "
                 f"(typisch: Bedienungshilfen-Permission fehlt)")
            return False
        return True
