#!/usr/bin/env python3
"""
HotkeyListener – Globaler Ctrl+Shift Hotkey via macOS Quartz CGEvent API
Läuft im Hintergrund-Thread, blockiert nicht den Haupt-Thread.
"""
import sys
import threading
import time

def _log(msg):
    print(f"[VoiceShift/hotkey] {msg}", flush=True)
    sys.stderr.flush()

try:
    from Quartz import (
        CGEventTapCreate, CGEventTapEnable,
        CFMachPortCreateRunLoopSource, CFRunLoopAddSource,
        CFRunLoopRun, CFRunLoopGetCurrent,
        kCGEventFlagsChanged,
        kCGEventFlagMaskControl, kCGEventFlagMaskShift,
        kCGHIDEventTap, kCGHeadInsertEventTap,
        kCGEventTapOptionDefault,
        CGEventGetFlags, CGEventSetFlags,
        kCGEventKeyDown, kCGEventKeyUp,
        kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        kCGEventRightMouseDown, kCGEventRightMouseUp,
        kCGEventOtherMouseDown, kCGEventOtherMouseUp,
        kCGEventLeftMouseDragged, kCGEventRightMouseDragged, kCGEventOtherMouseDragged,
        kCGEventScrollWheel,
        CGEventSourceFlagsState, kCGEventSourceStateHIDSystemState,
    )
    import CoreFoundation
    QUARTZ_AVAILABLE = True

    # Modifier, die WÄHREND der Aufnahme aus fremden Events entfernt werden,
    # damit ein gehaltenes Ctrl+Shift beim Durchklicken keine Shortcuts/Menüs
    # auslöst (Ctrl+Klick, Shift+Klick, Ctrl+Buchstabe …).
    _MODS_TO_STRIP = kCGEventFlagMaskControl | kCGEventFlagMaskShift

    # Event-Typen, deren Ctrl/Shift-Flags während der Aufnahme neutralisiert werden.
    _INPUT_EVENT_TYPES = frozenset((
        kCGEventKeyDown, kCGEventKeyUp,
        kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        kCGEventRightMouseDown, kCGEventRightMouseUp,
        kCGEventOtherMouseDown, kCGEventOtherMouseUp,
        kCGEventLeftMouseDragged, kCGEventRightMouseDragged, kCGEventOtherMouseDragged,
        kCGEventScrollWheel,
    ))

    # Tap-Maske: unser Hotkey (flagsChanged) + alle Eingabe-Events zum Strippen.
    _TAP_MASK = (1 << kCGEventFlagsChanged)
    for _t in _INPUT_EVENT_TYPES:
        _TAP_MASK |= (1 << _t)
except ImportError as e:
    QUARTZ_AVAILABLE = False
    _log(f"Quartz Import fehlgeschlagen: {e}")

# CGEventType-Werte für Tap-Disabled-Events (PyObjC exportiert die Konstanten
# nicht immer einheitlich, daher hardcodiert aus CGEventTypes.h):
_TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE   # kCGEventTapDisabledByTimeout
_TAP_DISABLED_BY_USER    = 0xFFFFFFFF   # kCGEventTapDisabledByUserInput

BOTH_MODIFIERS = None  # wird nach Import gesetzt

class HotkeyListener:
    # Während der Aufnahme: so oft wird der physische Tastenstatus abgeglichen.
    _RESYNC_INTERVAL = 0.25

    def __init__(self, on_press, on_release):
        self.on_press   = on_press
        self.on_release = on_release
        self._thread    = None
        self._active    = False   # Debounce: verhindert mehrfaches Auslösen
        self._flag_lock = threading.Lock()
        self._press_gen = 0       # beendet den Abgleich-Thread einer alten Aufnahme

    # ── Entscheidungslogik (ohne Quartz testbar) ─────────────────────────
    def _on_flags(self, ctrl: bool, shift: bool):
        if ctrl and shift:
            self._press()
        else:
            self._release()

    def _press(self):
        with self._flag_lock:
            if self._active:
                return
            self._active = True
            self._press_gen += 1
            gen = self._press_gen
        _log("→ on_press()")
        self.on_press()
        threading.Thread(target=self._resync_loop, args=(gen,), daemon=True).start()

    def _release(self):
        with self._flag_lock:
            if not self._active:
                return
            self._active = False
        _log("→ on_release()")
        self.on_release()

    def _resync(self):
        """Aufnahme beenden, wenn Ctrl+Shift physisch nicht mehr gehalten wird.

        Das Release-Event allein reicht nicht: macOS schaltet den Tap bei Last
        ab (DisabledByTimeout) oder unterdrückt Events (Passwortfelder,
        Sperrbildschirm). Fiel das Loslassen in so ein Fenster, lief die
        Aufnahme endlos – und Shift wurde weiter aus jedem Tastendruck entfernt.
        """
        if self._active and not self._modifiers_held():
            _log("Loslassen verpasst – per Tastenstatus-Abgleich erkannt")
            self._release()

    def _resync_loop(self, gen):
        while self._active and self._press_gen == gen:
            time.sleep(self._RESYNC_INTERVAL)
            self._resync()

    def _modifiers_held(self) -> bool:
        flags = CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState)
        return bool(flags & kCGEventFlagMaskControl) and bool(flags & kCGEventFlagMaskShift)

    def start(self):
        if not QUARTZ_AVAILABLE:
            _log("Quartz nicht verfügbar – Hotkey deaktiviert")
            return
        _log("Starte Hotkey-Listener-Thread …")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        tap_holder = {"tap": None}  # closure-shared, damit Callback re-enablen kann

        def callback(proxy, event_type, event, refcon):
            try:
                # macOS deaktiviert den Tap, wenn der Callback einmal zu langsam
                # war oder das System unter Last stand. Ohne Re-Enable bleibt
                # er stumm und der Hotkey wirkt "kaputt". Beide Disable-Events
                # behandeln, indem wir den Tap sofort wieder einschalten.
                if event_type in (_TAP_DISABLED_BY_TIMEOUT, _TAP_DISABLED_BY_USER):
                    _log(f"Tap disabled (type={event_type:#x}) → re-enable")
                    if tap_holder["tap"] is not None:
                        CGEventTapEnable(tap_holder["tap"], True)
                    # Während der Tap aus war, kann das Loslassen verloren sein.
                    self._resync()
                    return event

                # ── Hotkey-Erkennung: nur flagsChanged wertet den Modifier-Status aus.
                if event_type == kCGEventFlagsChanged:
                    flags = CGEventGetFlags(event)
                    self._on_flags(ctrl=bool(flags & kCGEventFlagMaskControl),
                                   shift=bool(flags & kCGEventFlagMaskShift))
                    return event   # flagsChanged unverändert weiterreichen

                # ── Während der Aufnahme: Ctrl/Shift aus fremden Eingabe-Events
                # entfernen, damit das gehaltene Hotkey-Modifier-Paar beim
                # Durchklicken keine Shortcuts/Kontextmenüs auslöst.
                if self._active and event_type in _INPUT_EVENT_TYPES:
                    flags = CGEventGetFlags(event)
                    stripped = flags & ~_MODS_TO_STRIP
                    if stripped != flags:
                        CGEventSetFlags(event, stripped)
            except Exception as e:
                _log(f"Callback-Exception: {e!r}")

            return event   # Event weiterreichen (nicht blockieren)

        tap = CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            _TAP_MASK,
            callback,
            None
        )

        if not tap:
            _log(
                "CGEventTap konnte NICHT erstellt werden. "
                "→ Bedienungshilfen-Berechtigung fehlt. "
                "→ Systemeinstellungen > Datenschutz & Sicherheit > Bedienungshilfen → '+' → "
                "/Applications/VoiceShift.app"
            )
            return

        tap_holder["tap"] = tap   # Callback-closure-Referenz für Re-Enable

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(
            CFRunLoopGetCurrent(),
            source,
            CoreFoundation.kCFRunLoopDefaultMode
        )
        CGEventTapEnable(tap, True)
        _log("CGEventTap aktiv – Hotkey Ctrl+Shift hört zu.")
        CFRunLoopRun()
