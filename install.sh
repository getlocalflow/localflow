#!/bin/bash
# LocalFlow installer for macOS.
# Usage:  curl -fsSL https://raw.githubusercontent.com/getlocalflow/localflow/main/install.sh | bash
# Safe to re-run at any time; it picks up where it left off.
set -euo pipefail

REPO_URL="${LOCALFLOW_REPO_URL:-https://github.com/getlocalflow/localflow.git}"
DIR="${LOCALFLOW_DIR:-$HOME/LocalFlow}"
LABEL="${LOCALFLOW_LABEL:-com.localflow.daemon}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
note() { printf '\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mProblem: %s\033[0m\n%s\n' "$1" "${2:-}"; exit 1; }

# Everything below runs inside main(), called on the LAST line of this file.
# Under `curl | bash`, bash reads the script from the pipe as it executes;
# without this wrapper, any child process that reads stdin (Homebrew does)
# swallows the rest of the script and the install silently stops early.
# The wrapper forces bash to parse the whole file before running anything.
main() {

step "Checking your Mac"
[ "$(uname)" = "Darwin" ] || die "This installer is for macOS only." \
  "On Windows, download LocalFlow-Setup.exe from https://github.com/getlocalflow/localflow/releases and see docs/INSTALL-WINDOWS.md."
macver=$(sw_vers -productVersion)
[ "${macver%%.*}" -ge 13 ] || die "LocalFlow needs macOS 13 (Ventura) or newer." \
  "This Mac is on macOS $macver."
command -v git >/dev/null 2>&1 || xcode-select --install || true
command -v git >/dev/null 2>&1 || die "git is not available yet." \
  "A popup should be asking to install Apple's Command Line Tools. Click Install, wait for it to finish, then run this installer again."

step "Checking for Homebrew (the standard Mac tool installer)"
if ! command -v brew >/dev/null 2>&1 && [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
if ! command -v brew >/dev/null 2>&1 && [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
if ! command -v brew >/dev/null 2>&1; then
  note "Homebrew is not installed. It is the standard, safe way to install"
  note "developer tools on a Mac (https://brew.sh). Installing it now."
  note "You may be asked for your Mac login password. That is normal."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/null \
    || die "Homebrew installation failed." "Visit https://brew.sh, follow its one-line install instructions, then re-run this installer."
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

step "Checking for Python 3.11 or newer"
PY=""
for cand in "$(brew --prefix)/bin/python3" python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    minor=$("$cand" -c 'import sys; print(sys.version_info[1] if sys.version_info[0] == 3 else 0)' 2>/dev/null || echo 0)
    if [ "$minor" -ge 11 ]; then PY="$(command -v "$cand")"; break; fi
  fi
done
if [ -z "$PY" ]; then
  note "Installing Python (one-time, ~1 minute)."
  brew install python </dev/null || die "Could not install Python." "Run 'brew doctor' to see what is wrong with Homebrew, then re-run this installer."
  PY="$(brew --prefix)/bin/python3"
fi
note "Using Python: $PY"

step "Downloading LocalFlow"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only || note "Could not update the existing copy; continuing with what is there."
else
  [ -e "$DIR" ] && die "$DIR already exists but is not a LocalFlow download." \
    "Move or rename that folder, then run the installer again."
  git clone "$REPO_URL" "$DIR" || die "Could not download LocalFlow." "Check your internet connection and re-run this installer."
fi
cd "$DIR"

step "Setting up LocalFlow's own Python environment (2-5 minutes)"
if [ -d venv ] && ! ./venv/bin/python3 -c "import sys" >/dev/null 2>&1; then
  note "Your previous LocalFlow environment looks broken (this happens after Python upgrades). Rebuilding it."
  rm -rf venv
fi
[ -d venv ] || "$PY" -m venv venv || die "Could not create LocalFlow's Python environment." "Re-run this installer. If it keeps failing, run 'brew reinstall python' first."
./venv/bin/pip install --quiet --upgrade pip \
  && ./venv/bin/pip install --quiet -r requirements.txt \
  || die "Could not install LocalFlow's components." "Check your internet connection and re-run this installer. If it keeps failing, delete the folder $DIR/venv and re-run."

step "Downloading the speech model (~1.6 GB, one time; ~10 min on typical Wi-Fi)"
./venv/bin/python3 - <<'PYEOF' || die "Could not download the speech model." "This is usually a network hiccup. Check your internet connection and re-run this installer; the download resumes where it left off."
from faster_whisper import download_model
download_model("large-v3-turbo")
print("Speech model ready.")
PYEOF

step "Creating your personal word lists"
[ -f dictionary.txt ]    || cp dictionary.sample.txt dictionary.txt
[ -f replacements.json ] || cp replacements.sample.json replacements.json
# NOTE: config.toml is intentionally NOT created here. Its absence tells
# LocalFlow this is a first run, which opens the setup window automatically.

step "Setting LocalFlow to start automatically"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__LOCALFLOW_DIR__|$DIR|g" -e "s|__LABEL__|$LABEL|g" \
  com.localflow.daemon.plist.template > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" || die "Could not set LocalFlow to start automatically." "Log out of your Mac, log back in, and re-run this installer."

step "Done installing. One last thing: permissions."
note "A LocalFlow setup window will open in a few seconds and walk you"
note "through 3 macOS permissions with live checkmarks:"
note "  1. Microphone            (so it can hear you)"
note "  2. Input Monitoring      (so the shortcut key works)"
note "  3. Accessibility         (so it can type text for you)"
note ""
note "When the checkmarks are green: press Ctrl+Option+Cmd+D, speak,"
note "press it again, and your words appear wherever your cursor is."
note ""
note "Full guide: $DIR/docs/INSTALL-MAC.md"

}
main "$@"
