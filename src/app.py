#!/usr/bin/env python3
"""
VoiceShift – Haupteinstiegspunkt
Startet die macOS Menu Bar App
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logsetup
logsetup.install()

from menu_bar import VoiceShiftApp

if __name__ == "__main__":
    VoiceShiftApp().run()
