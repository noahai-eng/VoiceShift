#!/usr/bin/env python3
"""Tests für recordings – was mit einer Aufnahme-Datei nach der Aufnahme passiert.

Regel: gelöscht wird nur, was erledigt ist (eingefügt oder nachweislich leer).
Alles andere bleibt liegen und wird beim nächsten Start nachgeholt – oder,
wenn schon die Transkription scheitert, in failed/ aufgehoben.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from recordings import discard, pending, quarantine  # noqa: E402


def _touch(path, content=b""):
    with open(path, "wb") as f:
        f.write(content)


def test_pending_findet_liegengebliebene_wavs_in_aufnahmereihenfolge(tmp_path):
    _touch(tmp_path / "20260930-101500-bbbbbb.wav")
    _touch(tmp_path / "20260930-101000-aaaaaa.wav")
    _touch(tmp_path / "20260930-101000-aaaaaa.json", b"{}")

    names = [os.path.basename(p) for p in pending(str(tmp_path))]

    assert names == ["20260930-101000-aaaaaa.wav", "20260930-101500-bbbbbb.wav"]


def test_pending_rettet_verwaiste_pcm_nach_absturz(tmp_path):
    np.array([1, 2], dtype=np.int16).tofile(tmp_path / "20260930-1-x.pcm")

    found = pending(str(tmp_path))

    assert [os.path.basename(p) for p in found] == ["20260930-1-x.wav"]
    assert not (tmp_path / "20260930-1-x.pcm").exists()


def test_pending_ohne_ordner_ist_leer(tmp_path):
    assert pending(str(tmp_path / "gibt-es-nicht")) == []


def test_discard_loescht_audio_und_metadaten(tmp_path):
    wav = tmp_path / "a.wav"
    _touch(wav)
    _touch(tmp_path / "a.json", b"{}")

    discard(str(wav))

    assert os.listdir(tmp_path) == []


def test_quarantine_verschiebt_nach_failed(tmp_path):
    wav = tmp_path / "a.wav"
    _touch(wav, b"RIFF")
    _touch(tmp_path / "a.json", b"{}")
    failed = tmp_path / "failed"

    quarantine(str(wav), str(failed))

    assert sorted(os.listdir(failed)) == ["a.json", "a.wav"]
    assert not wav.exists()
    # failed/ wird von pending() nicht erneut aufgegriffen (kein Endlos-Loop)
    assert pending(str(tmp_path)) == []
