#!/bin/bash
set -e

echo "🎙️  VoiceShift – Setup wird gestartet..."
echo ""

# 1. Homebrew
if ! command -v brew &>/dev/null; then
  echo "📦 Homebrew wird installiert..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
  eval "$(/opt/homebrew/bin/brew shellenv)"
else
  echo "✅ Homebrew bereits vorhanden"
fi

# 2. cmake
if ! command -v cmake &>/dev/null; then
  echo "🔧 cmake wird installiert..."
  brew install cmake
else
  echo "✅ cmake bereits vorhanden"
fi

# 3. Python packages
echo "🐍 Python-Pakete werden installiert..."
pip3 install pyobjc-framework-Cocoa pyobjc-framework-ApplicationServices \
             pyobjc-framework-AVFoundation sounddevice numpy rumps

# 4. whisper.cpp
WHISPER_DIR="$HOME/.voiceshift/whisper.cpp"
if [ ! -d "$WHISPER_DIR" ]; then
  echo "🤖 whisper.cpp wird geklont..."
  mkdir -p "$HOME/.voiceshift"
  git clone https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR"
fi

echo "⚙️  whisper.cpp wird kompiliert (M1-optimiert)..."
cd "$WHISPER_DIR"
cmake -B build -DWHISPER_METAL=1 -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(sysctl -n hw.logicalcpu)

# 5. Modell herunterladen (small ~466 MB)
MODEL_PATH="$HOME/.voiceshift/models/ggml-small.bin"
if [ ! -f "$MODEL_PATH" ]; then
  echo "⬇️  Whisper 'small' Modell wird heruntergeladen (~466 MB)..."
  mkdir -p "$HOME/.voiceshift/models"
  bash "$WHISPER_DIR/models/download-ggml-model.sh" small "$HOME/.voiceshift/models"
fi

echo ""
echo "✅ Setup abgeschlossen!"
echo ""
echo "▶️  App starten mit:  python3 src/app.py"
