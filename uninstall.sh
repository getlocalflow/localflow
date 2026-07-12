#!/bin/bash
# LocalFlow uninstaller. Run:  bash ~/LocalFlow/uninstall.sh
set -uo pipefail

DIR="${LOCALFLOW_DIR:-$HOME/LocalFlow}"
LABEL="${LOCALFLOW_LABEL:-com.localflow.daemon}"

echo "Stopping LocalFlow..."
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "LocalFlow will no longer start automatically."

# Safety: refuse to delete anything that does not look like a LocalFlow install.
if [ "$DIR" = "/" ] || [ "$DIR" = "$HOME" ] || [ ! -f "$DIR/localflow.py" ]; then
  if [ -d "$DIR" ]; then
    echo "Refusing to delete $DIR automatically: it does not look like a LocalFlow folder."
    echo "If you are sure, delete it yourself by dragging it to the Trash."
  fi
else
  if [ -d "$DIR" ]; then
    printf 'Delete %s too? This removes the app AND your dictation history. [y/N] ' "$DIR"
    read -r ans
    if [ "${ans:-n}" = "y" ] || [ "${ans:-n}" = "Y" ]; then
      rm -rf "$DIR"
      echo "Removed $DIR."
    else
      echo "Kept $DIR. Delete it later by dragging it to the Trash."
    fi
  fi
fi

echo ""
echo "Optional leftovers you can also remove:"
echo "  Startup log:  rm -f /tmp/localflow-launchd.log"
echo "  Logs:         rm -rf ~/Library/Logs/LocalFlow"
echo "  Speech model (1.6 GB, shared with other Whisper apps on this Mac):"
echo "    rm -rf ~/.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
echo ""
echo "Permissions you granted (Microphone, Accessibility, Input Monitoring)"
echo "can be removed in System Settings > Privacy & Security."
echo "Uninstall complete."
