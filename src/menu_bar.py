#!/usr/bin/env python3
"""
Menu Bar Controller – rumps-basierte macOS Menüleisten-App
"""
import glob
import os
import queue
import subprocess
import sys
import threading
import time

import AppKit
import rumps


def _log(msg):
    print(f"[VoiceShift] {msg}", flush=True)
    sys.stderr.flush()

from recorder import AudioRecorder, REC_DIR, read_meta
from recordings import discard, pending, quarantine
from transcriber import Transcriber
from injector import TextInjector
from hotkey import HotkeyListener
from pipeline import SerialPipeline

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets",
)
ICON_SYMBOL       = os.path.join(_ASSETS_DIR, "icon_symbol.png")
ICON_ICNS         = os.path.join(_ASSETS_DIR, "icon.icns")
ANIM_FRAME_PATHS  = sorted(glob.glob(os.path.join(_ASSETS_DIR, "animation", "frame_*.png")))
ANIM_INTERVAL     = 0.08          # Sekunden pro Frame (~12.5 FPS)
MENUBAR_PT        = 18            # Zielhöhe in Punkten (skaliert in Menüleiste)


def _load_template_image(path):
    """Lädt PNG als NSImage mit Template-Flag (macOS tintet automatisch)."""
    img = AppKit.NSImage.alloc().initByReferencingFile_(path)
    img.setSize_(AppKit.NSMakeSize(MENUBAR_PT, MENUBAR_PT))
    img.setTemplate_(True)
    return img

MODE_LABELS = {
    "normal":    "Modus: Normal",
    "formal":    "Modus: Formell",
    "translate": "Modus: Übersetzen (DE→EN)",
    "structure": "Modus: Struktur",
}

LANG_LABELS = {
    "de": "Sprache: Deutsch",
    "en": "Sprache: Englisch",
}

class VoiceShiftApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="VoiceShift",
            title="",
            icon=ICON_SYMBOL if os.path.exists(ICON_SYMBOL) else None,
            template=True,
            quit_button=None,
        )
        self.recording     = False
        self.current_mode  = "normal"
        self.current_lang  = "de"
        # Beim Aufnahme-Start erfasstes Zielfenster (dort landet der Text später,
        # auch wenn der Nutzer inzwischen woanders ist – gesetzt im Worker-Thread).
        self._target_pid   = None
        self._target_name  = None
        # Hängt der Audio-Stopp, wird neu gestartet, sobald die Pipeline leer
        # ist; bis dahin werden keine neuen Aufnahmen angenommen.
        self._restart_pending = False
        # Für "Letztes Diktat kopieren" – falls es im falschen Fenster landete.
        self._last_text    = None
        self.recorder      = AudioRecorder()
        self.transcriber   = Transcriber()
        self.injector      = TextInjector()
        self._state_lock   = threading.Lock()

        # Audio-Start/-Stop laufen NIE im Hotkey-Callback-Thread, sondern auf
        # diesem dedizierten Worker. Grund: ein blockierender PortAudio-/
        # CoreAudio-Aufruf (bekannter HAL-Deadlock nach Geräte-Wechsel/Sleep)
        # würde sonst den Event-Tap-Thread und damit die ganze App einfrieren.
        self._cmd_queue = queue.Queue()
        self._worker    = threading.Thread(target=self._recording_worker, daemon=True)
        self._worker.start()

        # Transkription+Injection laufen STRENG NACHEINANDER auf einer eigenen
        # Pipeline. Früher bekam jede Aufnahme ihren eigenen Thread – zwei
        # gleichzeitige Injections verschränkten dann den Clipboard-Abschnitt
        # des Injectors und ein altes Diktat landete im Zielfenster.
        # Siehe pipeline.py für den belegten Ablauf.
        self._process_pipeline = SerialPipeline(
            self._process_item, name="voiceshift-process")
        self._process_pipeline.start()
        # Aufnahmen, die ein Absturz liegen gelassen hat, nachholen.
        self._recover_pending()

        # Bilder vorladen (alle als Template, keine Disk-I/O pro Frame)
        self._static_image = (_load_template_image(ICON_SYMBOL)
                              if os.path.exists(ICON_SYMBOL) else None)
        self._anim_images  = [_load_template_image(p) for p in ANIM_FRAME_PATHS]

        # Flag wird vom Hotkey-Thread gesetzt, vom Main-Thread-Timer gelesen
        self._anim_active   = False
        self._anim_idx      = 0
        self._was_animating = False
        # NSTimer MUSS auf dem Main-Thread gestartet werden – darum hier in __init__
        self._anim_timer    = rumps.Timer(self._tick, ANIM_INTERVAL)
        self._anim_timer.start()

        self.hotkey = HotkeyListener(
            on_press=self.start_recording,
            on_release=self.stop_recording
        )
        self._build_menu()
        self.hotkey.start()
        # Onboarding beim ersten Start
        self._check_first_run()

    # ── Notifications (rumps crasht ohne CFBundleIdentifier in der Python-Bin) ──
    def _safe_notify(self, message: str, title: str = "VoiceShift", subtitle: str = ""):
        try:
            rumps.notification(title, subtitle, message)
        except Exception as e:
            print(f"[VoiceShift] {title} – {message}  (notification suppressed: {e})")

    # ── Animation (läuft ausschließlich auf Main-Thread via NSTimer) ──
    def _set_status_image(self, nsimage):
        if nsimage is None:
            return
        try:
            btn = self._nsapp.nsstatusitem.button()
            if btn is not None:
                btn.setImage_(nsimage)
        except Exception:
            pass

    def _tick(self, _):
        try:
            if self._anim_active and self._anim_images:
                self._set_status_image(self._anim_images[self._anim_idx])
                self._anim_idx = (self._anim_idx + 1) % len(self._anim_images)
                self._was_animating = True
            elif self._was_animating:
                # Animation gerade beendet → statisches Symbol wiederherstellen
                self._set_status_image(self._static_image)
                self._anim_idx = 0
                self._was_animating = False
        except Exception:
            pass

    def _build_menu(self):
        # Items werden unter ihrem stabilen Basis-Titel (ohne ✓) angelegt.
        # _set_mode/_set_lang setzen nur das state-Image (✓), behalten aber
        # den ursprünglichen Key — sonst geht der Lookup nach dem ersten Wechsel kaputt.
        self._mode_items = {
            key: rumps.MenuItem(label, callback=getattr(self, f"set_mode_{key}"))
            for key, label in MODE_LABELS.items()
        }
        self._lang_items = {
            key: rumps.MenuItem(label, callback=getattr(self, f"set_lang_{key}"))
            for key, label in LANG_LABELS.items()
        }
        self._mode_items[self.current_mode].state = 1
        self._lang_items[self.current_lang].state = 1

        self.menu = [
            rumps.MenuItem("VoiceShift  |  Ctrl+Shift"),
            None,
            self._mode_items["normal"],
            self._mode_items["formal"],
            self._mode_items["translate"],
            self._mode_items["structure"],
            None,
            self._lang_items["de"],
            self._lang_items["en"],
            None,
            rumps.MenuItem("Letztes Diktat kopieren",      callback=self.copy_last_text),
            None,
            rumps.MenuItem("Berechtigungen prüfen",        callback=self.check_permissions),
            rumps.MenuItem("Onboarding / Hilfe",           callback=self.show_onboarding),
            None,
            rumps.MenuItem("Beenden",                      callback=rumps.quit_application),
        ]

    # ── Modus ──────────────────────────────────────────────────────────
    def set_mode_normal(self, _):    self._set_mode("normal")
    def set_mode_formal(self, _):    self._set_mode("formal")
    def set_mode_translate(self, _): self._set_mode("translate")
    def set_mode_structure(self, _): self._set_mode("structure")

    def _set_mode(self, mode):
        self.current_mode = mode
        for key, item in self._mode_items.items():
            item.state = 1 if key == mode else 0
        self._safe_notify(f"Modus: {MODE_LABELS[mode].split(': ')[1]}")

    # ── Sprache ────────────────────────────────────────────────────────
    def set_lang_de(self, _): self._set_lang("de")
    def set_lang_en(self, _): self._set_lang("en")

    def _set_lang(self, lang):
        self.current_lang = lang
        for key, item in self._lang_items.items():
            item.state = 1 if key == lang else 0

    # ── Aufnahme ───────────────────────────────────────────────────────
    # start_recording/stop_recording werden aus dem Hotkey-Thread aufgerufen.
    # Sie dürfen WEDER UI-Calls (self.icon / self.title) machen NOCH blockieren –
    # sie setzen nur Flags und reihen ein Kommando für den Worker-Thread ein.
    # Die eigentlichen (potenziell blockierenden) Audio-Aufrufe laufen im Worker.
    def start_recording(self):
        if self._restart_pending:
            _log("start_recording: Audio hängt, Neustart steht aus – ignoriert")
            self._safe_notify("Audio hängt – VoiceShift startet gleich neu. "
                              "Bitte kurz warten.", subtitle="Nicht aufgenommen")
            return
        with self._state_lock:
            if self.recording:
                _log("start_recording: bereits am Aufnehmen – ignoriert")
                return
            self.recording = True
        self._anim_active = True
        self._cmd_queue.put("start")

    def stop_recording(self):
        with self._state_lock:
            if not self.recording:
                _log("stop_recording: nichts am Aufnehmen – ignoriert")
                return
            self.recording = False
        self._anim_active = False
        self._cmd_queue.put("stop")

    # ── Worker-Thread: serialisiert alle Audio-Operationen ───────────────
    def _recording_worker(self):
        while True:
            cmd = self._cmd_queue.get()
            try:
                if cmd == "start":
                    # Zielfenster JETZT merken – bevor der Nutzer wechseln kann.
                    # Der Text wird später gezielt an diesen Prozess gepostet.
                    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
                    self._target_pid  = app.processIdentifier() if app else None
                    self._target_name = app.localizedName()     if app else None
                    _log(f"worker: Zielfenster erfasst → {self._target_name} (pid={self._target_pid})")
                    _log("worker: starte AudioRecorder …")
                    meta = {"target_pid": self._target_pid,
                            "target_name": self._target_name,
                            "lang": self.current_lang}
                    self._guarded_audio_call(
                        lambda: self.recorder.start(meta=meta), "start")
                    _log("worker: AudioRecorder läuft")
                elif cmd == "stop":
                    # ERST die WAV fertigstellen und einreihen, DANN den Stream
                    # stoppen: der Stopp kann im CoreAudio-Deadlock hängen, und
                    # das Diktat muss bis dahin schon sicher unterwegs sein.
                    audio_path = self.recorder.finish()
                    _log(f"worker: WAV geschrieben → {audio_path}")
                    if audio_path:
                        # Ziel-PID für DIESE Aufnahme festhalten, bevor eine neue
                        # Aufnahme sie überschreiben könnte. Einreihen statt Thread
                        # starten: die Verarbeitung läuft serialisiert und in
                        # Sprechreihenfolge.
                        self._process_pipeline.submit(
                            (audio_path, self._target_pid, False))
                    _log("worker: stoppe AudioRecorder …")
                    self._guarded_audio_call(self.recorder.teardown, "stop")
                    _log("worker: AudioRecorder gestoppt")
            except Exception as e:
                if cmd == "start":
                    with self._state_lock:
                        self.recording = False
                    self._anim_active = False
                _log(f"worker FEHLER ({cmd}): {e!r}")
                self._safe_notify(f"Aufnahme {cmd} fehlgeschlagen: {e}", subtitle="Fehler")

    # ── Watchdog gegen CoreAudio-/PortAudio-Deadlock ─────────────────────
    # Ein verklemmter HAL-Mutex lässt sich aus Python nicht mehr lösen – der
    # Worker-Thread steckt dann in einer C-Bibliothek fest. Statt die App ewig
    # hängen zu lassen, beenden wir den Prozess hart; der LaunchAgent startet
    # ihn automatisch neu (entspricht dem früher nötigen PC-Neustart).
    _AUDIO_TIMEOUT = 8.0   # Sekunden; ein gesunder start()/stop() braucht < 1 s

    def _guarded_audio_call(self, fn, label):
        watchdog = threading.Timer(self._AUDIO_TIMEOUT, self._on_audio_wedged, args=(label,))
        watchdog.start()
        try:
            return fn()
        finally:
            watchdog.cancel()

    # So lange darf ein bereits eingereihtes Diktat noch fertig werden, bevor
    # der Neustart erzwungen wird (Whisper braucht ~1 s pro 12 s Audio).
    _RESTART_GRACE = 180.0

    def _on_audio_wedged(self, label):
        _log(
            f"WATCHDOG: recorder.{label}() hängt seit {self._AUDIO_TIMEOUT:.0f}s – "
            f"vermutlich CoreAudio-/PortAudio-Deadlock. Neustart, sobald die "
            f"Verarbeitung fertig ist (LaunchAgent startet die App neu)."
        )
        self._restart_pending = True
        self._anim_active = False
        self._dump_hang_diagnostics(label)
        # Das Diktat liegt schon als WAV in der Pipeline – erst fertig
        # transkribieren und einfügen lassen, dann neu starten.
        deadline = time.time() + self._RESTART_GRACE
        while self._process_pipeline.busy() and time.time() < deadline:
            time.sleep(0.2)
        _log("WATCHDOG: erzwinge Neustart")
        sys.stdout.flush()
        sys.stderr.flush()
        # os._exit umgeht den festsitzenden Thread und die Python-Cleanup-Phase,
        # die selbst auf dem CoreAudio-Lock hängen bleiben würde.
        os._exit(1)

    def _dump_hang_diagnostics(self, label):
        """Native Stacks aller Threads sichern – Beweis für die nächste Analyse."""
        log_dir = os.path.expanduser("~/.voiceshift/logs")
        path = os.path.join(log_dir, f"hang-{label}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        try:
            os.makedirs(log_dir, exist_ok=True)
            subprocess.run(["sample", str(os.getpid()), "1", "-file", path],
                           capture_output=True, timeout=20)
            _log(f"WATCHDOG: Thread-Stacks gesichert → {path}")
        except Exception as e:
            _log(f"WATCHDOG: sample fehlgeschlagen: {e!r}")

    def _recover_pending(self):
        found = pending(REC_DIR)
        if found:
            _log(f"Wiederherstellung: {len(found)} liegengebliebene Aufnahme(n) → nachholen")
        for audio_path in found:
            self._process_pipeline.submit((audio_path, None, True))

    def _process_item(self, item):
        """Pipeline-Einstiegspunkt – ein Eintrag pro Aufnahme, nie nebenläufig."""
        audio_path, target_pid, recovered = item
        self._process(audio_path, target_pid, recovered)

    def _process(self, audio_path, target_pid=None, recovered=False):
        # Die Aufnahme-Datei wird erst gelöscht, wenn der Text sicher
        # angekommen ist (im Ziel oder im Clipboard). Scheitert schon die
        # Transkription, wird sie in failed/ aufgehoben statt verworfen.
        lang = read_meta(audio_path).get("lang") or self.current_lang
        seconds = max(0, os.path.getsize(audio_path) - 44) / 32000
        _log(f"_process: starte Transkription von {audio_path} "
             f"({seconds:.1f}s Audio, lang={lang}, target_pid={target_pid}, "
             f"recovered={recovered})")
        t0 = time.time()
        try:
            text = self.transcriber.transcribe(audio_path, lang=lang, delete_after=False)
        except Exception as e:
            _log(f"_process FEHLER bei Transkription: {e!r}")
            quarantine(audio_path)
            self._safe_notify(f"{e} – Aufnahme aufgehoben in ~/.voiceshift/recordings/failed",
                              subtitle="Transkription fehlgeschlagen")
            return
        _log(f"_process: Transkription fertig nach {time.time() - t0:.1f}s, "
             f"len={len(text or '')}: {text!r}")
        if not text:
            _log("_process: leerer Text – nichts zu injizieren")
            discard(audio_path)
            return
        self._last_text = text

        if recovered:
            # Das ursprüngliche Zielfenster ist nach einem Absturz nicht mehr
            # verlässlich – nicht blind irgendwo einfügen.
            self.injector._set_clipboard(text)
            self._safe_notify("Ein abgebrochenes Diktat wurde gerettet – "
                              "es liegt im Clipboard (Cmd+V).", subtitle="Wiederhergestellt")
            discard(audio_path)
            return

        _log("_process: injiziere Text …")
        t1 = time.time()
        try:
            landed = self.injector.inject(text, target_pid=target_pid)
        except Exception as e:
            _log(f"_process FEHLER bei Injection: {e!r}")
            self.injector._set_clipboard(text)
            landed = False
        _log(f"_process: Injection abgeschlossen nach {time.time() - t1:.1f}s "
             f"(angekommen={landed})")
        if not landed:
            # Nie stillschweigend verlieren: der Text liegt im Clipboard.
            self._safe_notify(
                "Zielfenster nicht erreichbar – Text liegt im Clipboard (Cmd+V)",
                subtitle="Nicht eingefügt",
            )
        discard(audio_path)

    def copy_last_text(self, _):
        if not self._last_text:
            self._safe_notify("Noch kein Diktat in dieser Sitzung.")
            return
        self.injector._set_clipboard(self._last_text)
        self._safe_notify("Letztes Diktat liegt im Clipboard (Cmd+V).")

    # ── Hilfsfunktionen ────────────────────────────────────────────────
    def check_permissions(self, _):
        from permissions import check_and_request
        check_and_request()

    def show_onboarding(self, _):
        from onboarding import show_onboarding_window
        show_onboarding_window()

    def _check_first_run(self):
        import os
        flag = os.path.expanduser("~/.voiceshift/.onboarding_done")
        if not os.path.exists(flag):
            from onboarding import show_onboarding_window
            show_onboarding_window()
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            open(flag, "w").close()
