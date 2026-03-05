# beeper

Converts text to audio beeps. Each syllable of each word produces a sine-wave tone. Pitch is derived from the letters in the syllable, duration from its length. Punctuation and whitespace produce silence. Designed for streaming input.

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

Keep `beeper.py` and `beeper.toml` in the same directory.

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

### Custom config file

```bash
python beeper.py --config /path/to/custom.toml "hello world"
cat file.txt | python beeper.py --config /path/to/custom.toml
```

---

## Library Usage

`Beeper` exposes a simple streaming interface. Feed it text in any chunk size — character by character, line by line, or in larger blocks. Audio plays in a background thread as text arrives.

```python
from beeper import Beeper

beeper = Beeper()
beeper.feed("Hello, world.")
beeper.stop()  # flushes remaining word, drains audio queue, shuts down thread
```

### Custom config path

```python
from pathlib import Path
from beeper import Beeper

beeper = Beeper(config_path=Path("/path/to/custom.toml"))
beeper.feed("some text")
beeper.stop()
```

### Streaming example

```python
from beeper import Beeper

beeper = Beeper()

for chunk in some_streaming_source():
    beeper.feed(chunk)

beeper.stop()
```

### API

| Method | Description |
|---|---|
| `Beeper(config_path=...)` | Instantiate. Loads config, starts audio worker thread. |
| `.feed(chunk: str)` | Feed any string. Processes char by char. Non-blocking. |
| `.flush()` | Process any buffered trailing word. Called automatically by `stop()`. |
| `.stop()` | Flush, drain audio queue, shut down worker thread. Call once at end. |

---

## Configuration

All tuneable parameters live in `beeper.toml`.

```toml
[audio]
sample_rate = 44100
volume      = 0.4      # 0.0 – 1.0
attack_ms   = 6        # fade-in to prevent clicks
decay_ms    = 6        # fade-out to prevent clicks

[pitch]
freq_low    = 246.94   # frequency for letter-index 0 (low B)
freq_high   = 1046.50  # frequency for letter-index 25 (high C)

[pitch.letter_indices]
a = 0
b = 1
# ... one entry per letter, each mapped to a position 0–25 in the freq range
# rearranging these changes the pitch character of the output

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

Each letter is assigned an index 0–25 via `[pitch.letter_indices]`. The 26 indices map linearly to frequencies between `freq_low` and `freq_high`. For a given syllable, the letter indices are summed and taken mod 26 to produce the final pitch. Rearranging the letter index table in the toml is the primary way to change the sonic character of the output.

### How duration works

Duration in milliseconds = number of letters in syllable × `ms_per_letter`, clamped to `[min_ms, max_ms]`.
