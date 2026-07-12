# LocalFlow

Fully local, private voice dictation for your computer. Press a shortcut,
speak, press again, and clean punctuated text appears wherever your cursor
is. **Nothing you say ever leaves your machine.** No account, no cloud, no
subscription. The code is open, so you can check that promise yourself.

Powered by Whisper (large-v3-turbo) running entirely on your own CPU.

## Install

**Mac** (macOS 13 Ventura or newer, 8 GB RAM):

Open the Terminal app (press Cmd+Space, type "Terminal", press Return),
paste this line, and press Return:

    curl -fsSL https://raw.githubusercontent.com/getlocalflow/localflow/main/install.sh | bash

Then follow the setup window. Full walkthrough with screenshots:
[docs/INSTALL-MAC.md](docs/INSTALL-MAC.md)

**Windows**: coming soon (in progress).

## How to use

| Action | Result |
|---|---|
| Ctrl+Option+Cmd+D | start recording / stop and paste |
| Shift + the same shortcut | raw mode (types exactly what you said) |
| Esc while recording | cancel |
| Click the floating pill | cancel or dismiss |

A small pill appears at the bottom of your screen: waveform while
recording, spinner while transcribing, checkmark when your text lands.
Recordings stop automatically at 5 minutes.

Tip: if your mouse has spare side buttons, map one to Ctrl+Option+Cmd+D in
your mouse software (Logi Options+, for example) and dictation becomes a
single thumb click. See the install guide for a walkthrough.

## Make it yours

- `dictionary.txt` teaches it names and jargon it should spell correctly.
- `replacements.json` force-fixes words it keeps getting wrong.
- `history/` keeps your last 200 dictations (audio + text) so words are
  never lost. Menu bar icon > History.
- Everything else lives in the menu bar icon.

## Phase 2 (optional): smart formatting

With [Ollama](https://ollama.com) installed, LocalFlow can lightly clean up
your dictation (punctuation, filler words) with a small local AI model.
Still 100% on your machine:

    brew install ollama
    ollama pull qwen2.5:3b

Then flip `llm_enabled = true` in `config.toml`.

## Privacy

- Audio is recorded, transcribed, and stored only on your computer.
- LocalFlow makes no network connections except to download the speech
  model once at install time (from Hugging Face) and, if you enable
  Phase 2, to your own local Ollama.

## Uninstall

    bash ~/LocalFlow/uninstall.sh

## License

MIT
