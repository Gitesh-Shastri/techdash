#!/bin/sh
# Build Techdash.app (menu bar app + desktop widget) into ~/Applications and
# start it at login. Needs the personal Apple Development cert in the keychain and `brew install xcodegen`.
#   ./widget/build.sh            build, install, launch
#   ./widget/build.sh --no-login skip the login item
# Then: right-click the desktop > Edit Widgets > search "Tech Brief".
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
APP="$HOME/Applications/Techdash.app"
AGENT="$HOME/Library/LaunchAgents/com.techdash.widget.plist"
BUILD="$HERE/build"

mkdir -p "$HOME/Applications" "$HOME/techdash/cache"
cd "$HERE"
xcodegen -q
xcodebuild -project Techdash.xcodeproj -scheme Techdash -configuration Release \
  -derivedDataPath "$BUILD" -quiet build \
  CURRENT_PROJECT_VERSION="$(date +%s)"   # new build number, or chronod keeps the cached widget

pkill -x Techdash 2>/dev/null || true
pkill -x TechdashBar 2>/dev/null || true
rm -rf "$APP"
cp -R "$BUILD/Build/Products/Release/Techdash.app" "$APP"
# Register so the widget gallery and the techdash:// scheme find this copy.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"

if [ "$1" != "--no-login" ]; then
  cat > "$AGENT" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.techdash.widget</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>$APP</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
fi

open "$APP"
echo "installed: $APP"
