# Installing LocalFlow on Windows (beta)

Total time: about 15 minutes, most of it a one-time download.
Every step tells you exactly what to click and what you should see.
LocalFlow for Windows is in beta: it works, and we are still collecting
feedback from the first users. Nothing you say ever leaves your computer.

**What you need:** Windows 10 or 11, about 4 GB of free disk space, a
microphone (your laptop's built-in one is fine).

---

## Part 1: Download and install (about 3 minutes)

### Step 1: Download the installer

Go to https://github.com/getlocalflow/localflow/releases and click
**LocalFlow-Setup.exe** under the latest release's Assets.

✅ The file appears in your Downloads folder.

### Step 2: Open the installer

Double-click **LocalFlow-Setup.exe** in Downloads.

✅ You probably see a blue box saying "Windows protected your PC". This
is normal for new apps that have not bought a Microsoft certificate yet.

### Step 3: Get past the blue box

Click the small **More info** link, then the **Run anyway** button.

✅ The LocalFlow Setup wizard opens.

### Step 4: Click through the wizard

Click **Next** on each page (the suggested folder locations are fine).
When you reach the page with the checkbox "Start LocalFlow automatically
when I sign in", leave it checked and click **Next**. Then click
**Install**, and on the last page leave "Launch LocalFlow now" checked
and click **Finish**.

✅ A small window appears saying it is downloading the speech model
(about 1.6 GB, one time, roughly 10 minutes on typical Wi-Fi).

### Step 5: Wait for the model download

Leave the window alone until it closes by itself.

✅ When it finishes you see a small LocalFlow icon in your system tray
(the icons near the clock, bottom-right; click the ^ arrow if hidden).

---

## Part 2: Your first dictation (1 minute)

### Step 6: Open any app you can type in

Notepad, your email, a browser search box. Click so the cursor is blinking.

✅ You see a blinking text cursor where you clicked.

### Step 7: Press Ctrl+Alt+D

Hold Ctrl and Alt, tap D.

✅ Windows may ask "Let LocalFlow access your microphone?" the first
time: click **Yes/Allow**. A small pill appears at the bottom-center of
your screen with a moving waveform. It is listening.

### Step 8: Say something

Try: "Hello, this is my first dictation with LocalFlow. It runs entirely
on my own computer."

✅ The pill's waveform moves as you speak.

### Step 9: Press Ctrl+Alt+D again

✅ The pill shows a spinner for a moment, then a checkmark, and your
sentence appears at your cursor, with punctuation.

---

## Good to know

| Action | Result |
|---|---|
| Ctrl+Alt+D | start recording / stop and paste |
| Shift+Ctrl+Alt+D | raw mode (types exactly what you said) |
| Esc while recording | cancel |
| Click the pill | cancel or dismiss |
| Right-click the tray icon | menu: pause, history, dictionary, quit |

Your dictations (audio + text) are kept on your computer in
`%APPDATA%\LocalFlow\history` so words are never lost. Teach it names
and jargon: tray menu > Dictionary.

## If something is not working

| What you see | Fix |
|---|---|
| Pressing Ctrl+Alt+D does nothing | Right-click the tray icon: if it says "hotkey unavailable", quit LocalFlow (tray menu) and start it again from the Start menu |
| Pill appears but no text lands | Tray menu > Copy Last Transcript, then press Ctrl+V |
| "No microphone found" | Windows Settings > System > Sound > Input: pick your mic, speak, watch the bar move; then try again |
| Nothing pastes into a specific app | Some admin-elevated apps block synthetic keystrokes; your text is still in tray menu > Copy Last Transcript |
| Antivirus quarantines LocalFlow | This is a false positive that can happen with unsigned apps; restore it or reinstall, and tell us so we can improve the packaging |

Log for troubleshooting: `%APPDATA%\LocalFlow\logs\localflow.log`

## Uninstalling

Windows Settings > Apps > Installed apps > LocalFlow > Uninstall.
Your dictation history stays in `%APPDATA%\LocalFlow`; delete that folder
too if you want everything gone.
