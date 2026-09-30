#!/usr/bin/env python3
"""Tests für AudioRecorder – die Kernregel: ein Diktat geht NIE verloren.

Hintergrund (live belegt in /tmp/voiceshift.out.log, 2026-09-30):
  worker: stoppe AudioRecorder …
  WATCHDOG: recorder.stop() hängt seit 8s … Erzwinge Neustart
Das Stoppen des PortAudio-Streams hing (CoreAudio-HAL-Deadlock), der Watchdog
beendete die App – und die Aufnahme, die bis dahin NUR im Arbeitsspeicher
lag, war weg. Der Nutzer musste alles noch einmal einsprechen.

Neu: Audio landet schon während der Aufnahme auf der Platte, und die WAV-Datei
wird fertiggestellt, BEVOR der Stream gestoppt wird.
"""
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import recorder as recorder_mod  # noqa: E402
from recorder import AudioRecorder, pcm_to_wav  # noqa: E402


class FakeStream:
    """Ersetzt sd.InputStream; der Test ruft den Audio-Callback selbst auf."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def feed(self, samples):
        block = np.asarray(samples, dtype=np.int16).reshape(-1, 1)
        self.callback(block, len(block), None, None)


@pytest.fixture
def rec(tmp_path, monkeypatch):
    FakeStream.instances = []
    monkeypatch.setattr(recorder_mod.sd, "InputStream", FakeStream)
    return AudioRecorder(rec_dir=str(tmp_path))


def _read_wav(path):
    with wave.open(path) as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def test_audio_liegt_schon_waehrend_der_aufnahme_auf_der_platte(rec, tmp_path):
    """DER Bug: stirbt der Prozess mitten drin, muss das Audio schon sicher sein."""
    rec.start()
    FakeStream.instances[-1].feed([1, 2, 3, 4])
    FakeStream.instances[-1].feed([5, 6])

    pcm = [p for p in os.listdir(tmp_path) if p.endswith(".pcm")]
    assert len(pcm) == 1
    data = np.fromfile(os.path.join(tmp_path, pcm[0]), dtype=np.int16)
    assert data.tolist() == [1, 2, 3, 4, 5, 6]


def test_finish_stoppt_den_stream_nicht(rec):
    """Der Stream-Stopp ist die Stelle, die hängt – finish() darf sie nicht anfassen."""
    rec.start()
    stream = FakeStream.instances[-1]
    stream.feed([7, 8, 9])

    path = rec.finish()

    assert not stream.stopped and not stream.closed
    assert _read_wav(path).tolist() == [7, 8, 9]


def test_nach_finish_kommt_nichts_mehr_in_die_datei(rec):
    rec.start()
    stream = FakeStream.instances[-1]
    stream.feed([1, 1])
    path = rec.finish()
    stream.feed([9, 9])      # Nachzügler-Callback vor dem Stream-Stopp

    assert _read_wav(path).tolist() == [1, 1]


def test_teardown_stoppt_und_schliesst_den_stream(rec):
    rec.start()
    stream = FakeStream.instances[-1]
    rec.finish()
    rec.teardown()
    assert stream.stopped and stream.closed


def test_finish_ohne_aufnahme_liefert_none(rec):
    assert rec.finish() is None


def test_zwei_aufnahmen_landen_in_getrennten_dateien(rec):
    rec.start()
    FakeStream.instances[-1].feed([1])
    a = rec.finish()
    rec.teardown()
    rec.start()
    FakeStream.instances[-1].feed([2])
    b = rec.finish()

    assert a != b
    assert _read_wav(a).tolist() == [1]
    assert _read_wav(b).tolist() == [2]


def test_feste_blockgroesse(rec):
    """Ohne blocksize liefert PortAudio beim 48→16-kHz-Umrechnen ~1060
    Callbacks/s (gemessen) – jeder davon braucht das GIL."""
    rec.start()
    assert FakeStream.instances[-1].kwargs.get("blocksize") == recorder_mod.BLOCKSIZE
    assert recorder_mod.BLOCKSIZE >= 800


def test_metadaten_werden_neben_der_aufnahme_gespeichert(rec):
    rec.start(meta={"target_pid": 42, "lang": "de"})
    path = rec.finish()
    assert recorder_mod.read_meta(path) == {"target_pid": 42, "lang": "de"}


def test_pcm_to_wav_rettet_eine_verwaiste_aufnahme(tmp_path):
    """Nach einem harten Absturz liegt nur die .pcm da – sie muss lesbar werden."""
    pcm = tmp_path / "x.pcm"
    np.array([3, -3, 5], dtype=np.int16).tofile(pcm)

    wav = pcm_to_wav(str(pcm))

    assert not pcm.exists()
    assert _read_wav(wav).tolist() == [3, -3, 5]


def test_gescheiterter_start_hinterlaesst_keine_datei(tmp_path, monkeypatch):
    class BrokenStream(FakeStream):
        def start(self):
            raise RuntimeError("Mikrofon weg")
    monkeypatch.setattr(recorder_mod.sd, "InputStream", BrokenStream)
    rec = AudioRecorder(rec_dir=str(tmp_path))

    with pytest.raises(RuntimeError):
        rec.start(meta={"lang": "de"})

    assert os.listdir(tmp_path) == []
    assert rec.finish() is None
