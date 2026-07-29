#!/bin/bash
# Erzeugt /Applications/VoiceShift.app als Kopie von anaconda3/python.app,
# ergänzt NSMicrophoneUsageDescription + eigene Bundle-ID + ad-hoc Signatur.
# Notwendig, weil Anacondas python.app keine NSMicrophoneUsageDescription hat
# und macOS Sequoia+ daher den Mikrofon-Dialog stillschweigend unterdrückt.
set -e

SRC=/Users/noahschmidt/anaconda3/python.app
DST=/Applications/VoiceShift.app

if [ ! -d "$SRC" ]; then
  echo "FEHLER: $SRC existiert nicht."
  exit 1
fi

if [ -d "$DST" ]; then
  echo "🧹  Alte $DST entfernen …"
  rm -rf "$DST"
fi

echo "📦  Kopiere python.app → $DST …"
cp -R "$SRC" "$DST"

PLIST="$DST/Contents/Info.plist"
echo "🔧  Setze CFBundleIdentifier=com.voiceshift.app …"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.voiceshift.app" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleName VoiceShift" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string VoiceShift" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName VoiceShift" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string VoiceShift" "$PLIST"

echo "🔧  Setze NSMicrophoneUsageDescription …"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'VoiceShift transkribiert deine Sprache zu Text – Mikrofon-Zugriff ist erforderlich.'" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription 'VoiceShift transkribiert deine Sprache zu Text – Mikrofon-Zugriff ist erforderlich.'" "$PLIST"

echo "🔧  Setze LSUIElement (Menüleisten-App, kein Dock-Icon) …"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"

echo "✍️   Ad-hoc re-codesign (sonst weigert sich macOS, die geänderte Plist zu akzeptieren) …"
codesign --force --deep --sign - "$DST"

echo
echo "✅  $DST ist bereit."
echo "    Bundle-ID:   $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PLIST")"
echo "    Mic-Reason:  $(/usr/libexec/PlistBuddy -c 'Print :NSMicrophoneUsageDescription' "$PLIST")"
echo "    Executable:  $DST/Contents/MacOS/python"
