#!/usr/bin/env python3
"""Stresstest für die Text-Injection gegen ECHTE Fenster.

Prüft die Kernzusage: Text landet im gemerkten Zielfenster, auch wenn der
Nutzer inzwischen woanders arbeitet – und ohne ihm den Fokus zu klauen.

    /Applications/VoiceShift.app/Contents/MacOS/python scripts/stress_injection.py
    … --app Safari --rounds 5

Warum ein eigenes Skript und kein pytest: das hier braucht echte Fenster,
einen echten Window-Server und stiehlt zeitweise den Fokus. Die reine
Entscheidungslogik ist in tests/test_injector.py abgedeckt.

WICHTIG beim Rücklesen: eine App im HINTERGRUND liefert nicht zuverlässig ein
AXFocusedUIElement (WebKit baut den Baum ab). Deshalb wird zur Kontrolle das
Zielfenster kurz nach vorne geholt – erst danach ist der Wert verlässlich
lesbar. Wer das vergisst, misst falsch-negative "nichts angekommen".
"""
import argparse
import os
import subprocess
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import AppKit                      # noqa: E402
import ApplicationServices as AS   # noqa: E402
from injector import TextInjector  # noqa: E402

CASES = [
    ("kurz",       "Hallo Welt"),
    ("umlaute",    "Grüße über Öl, weiß größer"),
    ("anfuehrung", 'Er sagte "hallo" zu mir'),
    ("zeilen",     "Erste Zeile.\nZweite Zeile."),
    ("lang",       "Dies ist ein sehr langer diktierter Satz, wie er beim Sprechen "
                   "typischerweise entsteht, mit vielen Woertern und Kommas, damit "
                   "wir sehen ob beim Einfuegen Zeichen verloren gehen."),
    ("sehr_lang",  "Wort " * 120),
]

inj = TextInjector()


def app_by_name(name):
    return next((a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
                 if a.localizedName() == name), None)


def focused(pid):
    ax = AS.AXUIElementCreateApplication(pid)
    err, el = AS.AXUIElementCopyAttributeValue(ax, AS.kAXFocusedUIElementAttribute, None)
    return el if err == 0 else None


def _value(el):
    if el is None:
        return ""
    err, val = AS.AXUIElementCopyAttributeValue(el, AS.kAXValueAttribute, None)
    return val if err == 0 and isinstance(val, str) else ""


def read_result(pid):
    """Feldinhalt nach der Injection lesen.

    Zuerst über die Hintergrund-Feldsuche, SOFORT nach dem Schreiben: solange
    ist der AX-Baum der Ziel-App noch "warm". Holt man das Fenster stattdessen
    erst nach vorne, kann WebKit den Baum zwischenzeitlich abbauen und liefert
    gar kein Element mehr – das ergibt falsch-negative "nichts angekommen".
    Nur wenn das leer bleibt, wird zusätzlich im Vordergrund nachgesehen.
    """
    warm = _value(inj._find_text_field(pid))
    if warm:
        return warm
    inj._activate(pid)
    time.sleep(0.5)
    return _value(focused(pid))


def clear(pid):
    """Zielfeld leeren – im VORDERGRUND, mit Kontrolle.

    Nicht über die Hintergrund-Feldsuche: die liefert bei kaltem WebKit-Baum
    kein Element, das Feld bliebe ungeleert und die nächste Runde meldete den
    Rest der Vorrunde als "verstümmelt".
    """
    for _ in range(3):
        inj._activate(pid)
        time.sleep(0.3)
        el = focused(pid) or inj._find_text_field(pid)
        if el is not None:
            AS.AXUIElementSetAttributeValue(el, AS.kAXValueAttribute, "")
            time.sleep(0.2)
            if not _value(focused(pid) or inj._find_text_field(pid)):
                return True
    return False


def focus_text_field(pid):
    """Textfeld der Ziel-App explizit fokussieren.

    Nötig, weil `autofocus` nur beim ERSTEN Laden einer Seite feuert. Öffnet
    man dieselbe Datei erneut, landet man in einem bestehenden Tab ohne Fokus –
    der Test misst dann Fehlschläge, die keine sind.
    """
    root = AS.AXUIElementCreateApplication(pid)
    deadline = time.time() + 3.0

    def walk(el, depth=0):
        if depth > 14 or time.time() > deadline:
            return None
        err, role = AS.AXUIElementCopyAttributeValue(el, AS.kAXRoleAttribute, None)
        if err == 0 and role in ("AXTextArea", "AXTextField"):
            return el
        err, kids = AS.AXUIElementCopyAttributeValue(el, AS.kAXChildrenAttribute, None)
        for k in (kids or []):
            found = walk(k, depth + 1)
            if found is not None:
                return found
        return None

    field = walk(root)
    if field is not None:
        AS.AXUIElementSetAttributeValue(field, "AXFocused", True)
        time.sleep(0.4)
    return field is not None


def setup_target(appname):
    """Frisches, leeres Zieldokument öffnen und dessen PID liefern."""
    if appname == "Safari":
        path = "/tmp/voiceshift_stress.html"
        with open(path, "w") as f:
            f.write('<html><body style="font:14px system-ui;padding:20px">'
                    '<h3>VoiceShift Stresstest</h3>'
                    '<textarea autofocus rows=14 cols=70></textarea></body></html>')
    else:
        path = "/tmp/voiceshift_stress.txt"
        open(path, "w").close()
    subprocess.run(["open", "-a", appname, path], check=True)
    time.sleep(4)
    app = app_by_name(appname)
    if app is None:
        sys.exit(f"{appname} läuft nicht")
    pid = app.processIdentifier()
    if not focus_text_field(pid):
        sys.exit(f"kein Textfeld in {appname} gefunden – Fenster prüfen")
    return pid


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--app", default="TextEdit", help="Ziel-App (TextEdit, Safari, …)")
    p.add_argument("--away", default="Finder", help="App, in die der Nutzer wechselt")
    p.add_argument("--rounds", type=int, default=3)
    args = p.parse_args()

    tpid = setup_target(args.app)
    away = app_by_name(args.away)
    if away is None:
        sys.exit(f"{args.away} läuft nicht")
    print(f"Ziel: {args.app} pid={tpid} | Ablenk-App: {args.away}\n")

    results = []
    print(f"{'Runde':<6}{'Fall':<12}{'Ergebnis':<16}{'Detail'}")
    print("-" * 74)

    for rnd in range(1, args.rounds + 1):
        for case, text in CASES:
            inj._activate(tpid)
            time.sleep(0.4)
            focus_text_field(tpid)
            clear(tpid)
            time.sleep(0.3)

            # Nutzer wechselt weg – Vorbedingung des Features
            away.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
            t0 = time.time()
            while time.time() - t0 < 3 and inj._is_frontmost(tpid):
                time.sleep(0.02)
            if inj._is_frontmost(tpid):
                print(f"{rnd:<6}{case:<12}{'SETUP-FAIL':<16}Wechsel klappte nicht")
                continue

            t0 = time.time()
            ok = inj.inject(text, target_pid=tpid)
            dur = time.time() - t0
            stole_focus = inj._is_frontmost(tpid)   # VOR dem Ablesen messen!

            got = read_result(tpid)
            if got.strip() == text.strip():
                verdict = "OK" if not stole_focus else "OK(Fokus weg)"
                detail = f"{dur:.2f}s"
            elif not got.strip():
                verdict, detail = "LEER", f"nichts angekommen (inject meldete {ok})"
            else:
                verdict = "VERSTUEMMELT"
                detail = f"{len(got)}/{len(text)} Zeichen: {got[:40]!r}"
            results.append(verdict)
            print(f"{rnd:<6}{case:<12}{verdict:<16}{detail}")

    print("\n" + "=" * 74)
    good = sum(1 for r in results if r.startswith("OK"))
    print(f"ERGEBNIS: {good}/{len(results)} erfolgreich")
    for k, v in Counter(results).most_common():
        print(f"   {k:<16}{v}")
    return 0 if good == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
