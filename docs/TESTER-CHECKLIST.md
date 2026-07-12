# LocalFlow Windows - Founding Tester Checklist (10 minutes)

You are the first human to run LocalFlow on real Windows hardware. This
checklist covers the things automated tests cannot. Reply with the numbers
that passed and screenshots of anything that failed (plus
`%APPDATA%\LocalFlow\logs\localflow.log` if something broke).

1. Install: LocalFlow-Setup.exe ran (after More info > Run anyway) and the
   wizard completed without errors.
2. First run: the model-download window appeared, finished, and closed by
   itself; the tray icon appeared.
3. Mic permission: Windows asked about the microphone once and never again.
4. Dictation: Ctrl+Alt+D in Notepad -> speak -> Ctrl+Alt+D pastes correct,
   punctuated text.
5. Pill: waveform while speaking, spinner, then checkmark with a word count.
6. Esc while recording cancels (no paste, pill disappears).
7. Shift+Ctrl+Alt+D types the words verbatim (raw mode).
8. Tray menu: Pause Listening blocks the hotkey; unpause restores it.
9. Tray menu: Copy Last Transcript + Ctrl+V reproduces the last dictation.
10. History: tray menu > Open History Folder shows folders with audio.wav
    and text files.
11. Reboot: after signing back in, LocalFlow is in the tray and the hotkey
    works without starting anything manually.
12. Sleep/wake: close the lid (or Start > Sleep), wake the machine, and the
    hotkey still starts a dictation. If it does not, note it and quit +
    relaunch LocalFlow from the Start menu, then tell us it happened.
13. Uninstall (optional, if you are done testing): Settings > Apps removes
    it; no LocalFlow process remains in Task Manager.

Also tell us: Windows version, laptop/desktop model, which mic, and how
the transcription accuracy and speed felt.
