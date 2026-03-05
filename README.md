# beeptalk

Converts text to audio beeps. Each syllable of each word produces a sine-wave tone. Pitch is derived from the letters in the syllable, duration from its length. Punctuation and whitespace produce silence. Designed for streaming input.

Multiple "emotion" configs can be loaded from a directory at startup and swapped at any time, giving different tonal characters to the output.

## Installation

```bash
pip install pygame pyphen numpy
```

Python 3.11+ is required for `tomllib`. On 3.10 or older:

```bash
pip install tomli
```

Then swap the import in `beeper.py`:

```python
import tomli as tomllib
```

Keep `beeper.py` and your `.toml` config files in the same directory (or point to a config dir explicitly).

---

## CLI Usage

### Direct string

```bash
python beeper.py "hello world"
```

### Pipe from stdin

```bash
cat file.txt | python beeper.py
echo "some text" | python beeper.py -
```

Any command that writes to stdout works:

```bash
curl -s https://example.com/some.txt | python beeper.py
ls -la | python beeper.py
```

### Select an emotion

```bash
python beeper.py --emotion harmonic-minor "hello world"
cat file.txt | python beeper.py --emotion melodic-minor
```

If `--emotion` is omitted, the first config found alphabetically in the config directory is used.

### Custom config directory

```bash
python beeper.py --config-dir ./configs --emotion c-major "hello world"
```

On startup, the CLI prints the loaded emotions and active emotion to stderr so it doesn't interfere with piped stdout workflows.

---

## Emotions (included configs)

| Filename | Character |
|---|---|
| `beeper.toml` | C blues pentatonic — default, bluesy |
| `beeper-chromatic.toml` | Amodal — 26 linear frequencies, alien/robotic |
| `beeper-c-major.toml` | C major — bright, neutral, diatonic |
| `beeper-natural-minor.toml` | A natural minor — smooth and dark |
| `beeper-harmonic-minor.toml` | A harmonic minor — tense, dramatic |
| `beeper-melodic-minor.toml` | A melodic minor — jazzy, smooth |

Any `.toml` file in the config directory that passes validation is loaded automatically. Add new ones by dropping files into the directory — no code changes needed.

---

## Library Usage

`Beeper` exposes a simple streaming interface. Feed it text in any chunk size — character by character, line by line, or larger blocks. Audio plays in a background thread as text arrives.

```python
from beeper import Beeper

beeper = Beeper()
beeper.feed("Hello, world.")
beeper.stop()
```

### Custom config directory and starting emotion

```python
from pathlib import Path
from beeper import Beeper

beeper = Beeper(config_dir=Path("./configs"), emotion="harmonic-minor")
beeper.feed("some text")
beeper.stop()
```

### Swapping emotion mid-stream

Emotion changes take effect at the next word boundary.

```python
beeper = Beeper(emotion="c-major")
beeper.feed("this part is bright and happy")
beeper.set_emotion("harmonic-minor")
beeper.feed("this part is tense and dramatic")
beeper.stop()
```

### Inspecting loaded emotions

```python
beeper = Beeper()
print(beeper.emotions)  # list of all loaded config stems
print(beeper.emotion)   # currently active emotion
```

### Streaming example

```python
from beeper import Beeper

beeper = Beeper(emotion="melodic-minor")
for chunk in some_streaming_source():
    beeper.feed(chunk)
beeper.stop()
```

### API

| Method / Property | Description |
|---|---|
| `Beeper(config_dir, emotion)` | Instantiate. Loads all valid configs from dir, starts audio worker. |
| `.feed(chunk: str)` | Feed any string. Processes char by char. Non-blocking. |
| `.set_emotion(name: str)` | Switch active config. Takes effect at next word boundary. |
| `.flush()` | Process any buffered trailing word. Called automatically by `stop()`. |
| `.stop()` | Flush, drain audio queue, shut down worker thread. Call once at end. |
| `.emotion` | Currently active emotion name. |
| `.emotions` | List of all loaded emotion names. |

---

## Configuration

All tuneable parameters live in `.toml` files. Every file in the config directory that contains the required keys is loaded as an available emotion, keyed by filename stem.

```toml
[audio]
sample_rate = 44100
volume      = 0.4      # 0.0 – 1.0
attack_ms   = 6        # fade-in to prevent clicks
decay_ms    = 6        # fade-out to prevent clicks

[pitch]
# Frequencies indexed by letter_index % len(scale_frequencies).
# Wraps automatically, so any number of notes is valid.
scale_frequencies = [
  261.63,  # C4
  329.63,  # E4
  # ...
]

[pitch.letter_indices]
a = 0
b = 1
# ... one entry per letter a-z, each mapped to a position 0–25
# rearranging these changes which syllable sums land on which scale degrees

[duration]
ms_per_letter = 80     # base duration multiplier per letter in a syllable
min_ms        = 80     # floor
max_ms        = 600    # ceiling

[silence]
space_ms = 40          # space or tab
comma_ms = 80          # comma
other_ms = 160         # period, newline, all other punctuation

[language]
pyphen_lang = "en_US"  # any language supported by pyphen
```

### How pitch works

Each letter is assigned an index 0–25 via `[pitch.letter_indices]`. For a given syllable, the letter indices are summed and taken mod the length of `scale_frequencies` to select a frequency. This means the scale can have any number of notes — wrapping is automatic.

### How duration works

Duration in milliseconds = number of letters in syllable × `ms_per_letter`, clamped to `[min_ms, max_ms]`.

### Adding a new emotion

1. Create a new `.toml` file in your config directory following the format above.
2. Restart beeper — it will be loaded automatically.
3. Reference it by filename stem: `--emotion my-new-config` or `beeper.set_emotion("my-new-config")`.
