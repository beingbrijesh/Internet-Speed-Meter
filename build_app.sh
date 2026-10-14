#!/bin/zsh
set -e

cd "${0:A:h}"

APP_NAME="Speed Meter"
BUNDLE_ID="com.beingbrijesh.speedmeter"
VENV="$HOME/speedmeter-build"
ZIP="SpeedMeter-arm64.zip"

if [[ ! -f speed_meter.py ]]; then
  echo "speed_meter.py not found next to this script."
  exit 1
fi

echo "==> Stopping any running copy"
pkill -f speed_meter.py 2>/dev/null || true
pkill -f "$APP_NAME.app" 2>/dev/null || true
sleep 1

echo "==> Removing old build output"
rm -rf build dist "$APP_NAME.spec" "$ZIP"

if [[ ! -d "$VENV" ]]; then
  echo "==> Creating build environment"
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

echo "==> Installing build dependencies"
pip install --quiet --upgrade pip
pip install --quiet --upgrade pyinstaller rumps psutil pyobjc-framework-Cocoa certifi

echo "==> Building the app"
pyinstaller --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --hidden-import http.client --hidden-import ssl --hidden-import certifi \
  --exclude-module tkinter \
  speed_meter.py

echo "==> Hiding the Dock icon and re-signing"
plutil -replace LSUIElement -bool true "dist/$APP_NAME.app/Contents/Info.plist"
codesign --force --deep --sign - "dist/$APP_NAME.app"

echo "==> Packaging"
ditto -c -k --sequesterRsrc --keepParent "dist/$APP_NAME.app" "$ZIP"

deactivate

echo
echo "Done."
echo "App: $PWD/dist/$APP_NAME.app"
echo "Zip: $PWD/$ZIP"
echo "SHA-256:"
shasum -a 256 "$ZIP"
echo
echo "Test it:  open \"dist/$APP_NAME.app\""