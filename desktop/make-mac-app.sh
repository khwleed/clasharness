#!/usr/bin/env bash
# Build "Clash Harness.app" so you can start the desktop app from the Dock or Spotlight.
#   desktop/make-mac-app.sh                 installs into ~/Applications
#   desktop/make-mac-app.sh /Applications   or somewhere else
# The app runs this checkout's run.sh, so re-run this only if you move the repo.
set -euo pipefail

if [ "$(uname)" != Darwin ]; then
  echo "This builds a macOS app; on other systems run ./run.sh desktop." >&2
  exit 1
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:-$HOME/Applications}
app="$dest/Clash Harness.app"

mkdir -p "$app/Contents/MacOS"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Clash Harness</string>
  <key>CFBundleDisplayName</key><string>Clash Harness</string>
  <key>CFBundleIdentifier</key><string>local.clasharness.desktop</string>
  <key>CFBundleExecutable</key><string>clash-harness</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.3</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$app/Contents/MacOS/clash-harness" <<SCRIPT
#!/usr/bin/env bash
# Apps started from Finder get a bare PATH; add Homebrew's so scrcpy and adb are found.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
cd "$repo" || exit 1
if [ ! -f config.toml ]; then
  osascript -e 'display alert "Clash Harness isn'"'"'t set up yet" message "Run ./run.sh once in Terminal (in $repo) and fill in config.toml first."'
  exit 1
fi
mkdir -p var
exec ./run.sh desktop >> var/desktop.log 2>&1
SCRIPT
chmod +x "$app/Contents/MacOS/clash-harness"

echo "Built $app"
echo "Open it from Spotlight or drag it to the Dock. Logs go to $repo/var/desktop.log."
