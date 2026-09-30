#!/usr/bin/env python3
"""Tests für logsetup – Logs müssen einen Neustart überleben.

Hintergrund: die LaunchAgent-Logs liegen in /tmp, das macOS bei jedem
Neustart leert. Als lange Diktate verloren gingen, war dadurch kein einziger
Fehlschlag mehr nachvollziehbar – und die Zeilen hatten keine Uhrzeit.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from logsetup import TimestampTee, rotate_if_large  # noqa: E402


def test_jede_zeile_bekommt_einen_zeitstempel_und_landet_in_beiden_zielen():
    console, logfile = io.StringIO(), io.StringIO()
    tee = TimestampTee(console, logfile)

    tee.write("[VoiceShift] eins\n[VoiceShift] zw")
    tee.write("ei\n")

    for out in (console, logfile):
        lines = out.getvalue().splitlines()
        assert len(lines) == 2
        assert all(re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \[VoiceShift\]", l)
                   for l in lines)
        assert lines[1].endswith("zwei")


def test_rotation_ab_grenze(tmp_path):
    log = tmp_path / "voiceshift.log"
    log.write_bytes(b"x" * 100)

    rotate_if_large(str(log), max_bytes=50)

    assert not log.exists()
    assert (tmp_path / "voiceshift.log.1").read_bytes() == b"x" * 100


def test_keine_rotation_unter_grenze(tmp_path):
    log = tmp_path / "voiceshift.log"
    log.write_bytes(b"x" * 10)

    rotate_if_large(str(log), max_bytes=50)

    assert log.exists()
