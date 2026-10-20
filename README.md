# Pixel Sort Studio

A desktop app (Python + Tkinter) that sorts the pixels of a photo along a chosen direction.
It animates every step of the sorting algorithm and lets you limit the effect with a brush mask
and a threshold on the sort key.

## Features

- **Any direction** (0–359°, compass and 45° presets). The image is never rotated.
- **Sort key:** brightness, hue, saturation or value (HSV), with optional quantization and descending order.
- **Three algorithms** you can watch step by step: odd-even, bitonic and bubble.
- **Threshold:** only pixels whose key lies inside a chosen range are sorted.
- **Brush mask:** protect a painted area, or sort only inside it.
- **Chained passes:** after each pass change the direction or key and sort again. Undo is supported.
- **Five views:** Animation, Result (full resolution), Motion map, Mask, Affected.
- **Export** the result to PNG/JPEG and the animation to GIF.

## Requirements

- Python 3 (developed and tested with 3.12)
- **Tkinter**, the GUI toolkit. It is a system package, so `pip` cannot install it:
  - Debian / Ubuntu / Linux Mint: `sudo apt install python3-tk`
  - Windows / macOS: included with the installer from python.org
- `numpy` and `Pillow`, installed with `pip install -r requirements.txt`

If Tkinter is missing, `python main.py` stops with a message that says how to install it.

## Installation

### Linux (Debian, Ubuntu, Linux Mint)

```bash
sudo apt install python3-tk python3-venv git
git clone https://github.com/omroz14/Pixel-sorter.git
cd Pixel-sorter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Windows

Tkinter is part of the python.org installer. In PowerShell:

```powershell
git clone https://github.com/omroz14/Pixel-sorter.git
cd Pixel-sorter
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py main.py
```

## Quick start

1. **Choose an image** (button or key `O`).
2. Set the **direction** and the **sort key**, for example 90° and brightness.
3. Press **Play** (or `Space`). When the pass ends it is committed as a step.
4. Change the settings and play again to apply another pass on top of the previous result.
5. The **Result** view shows the image at full resolution, and **Save result** writes it to a file.

## The interface

| Area        | Contents                                                                      |
|-------------|-------------------------------------------------------------------------------|
| Left panel  | What and how to sort: Image, Direction, Sort by, Algorithm, Parameters, Threshold. |
| Center      | The view selected in the tab bar.                                             |
| Right panel | Mask, Animation (play, speed, undo, reset) and Export.                        |

Both panels scroll with the mouse wheel when the window is short.

## Key quantization

The sort key of a pixel is the chosen property `u` in [0, 1], for example its brightness.
Quantization first snaps `u` to one of `L` levels (the slider goes from 8 to 256, where 256 means off):

```
level = min(floor(u · L), L − 1)
```

Pixels on the same level tie on the key, and the tie is broken by their original position, so
they keep their relative order. Only the order of the pixels changes, never their colors.

- **Full (off):** practically exact sorting (65535 levels internally).
- **Fewer levels:** a coarser sort. With 8 levels, brightness 0.000–0.125 is one level,
  0.125–0.250 the next, and so on. Pixels inside one level are not rearranged by the sort,
  so more of the original arrangement survives.

## Threshold

The two sliders in the **Threshold** section set a range (in percent) of the property chosen in
**Sort by**. Only a pixel whose property lies in that range is sorted. The other pixels stay in
place and interrupt the line, so sorting happens in separate segments, as in classic glitch art.

Example: sorting by brightness with a range of 30–70% rearranges only the mid tones. The
brightest and darkest areas stay untouched and mark the borders between segments.

- The **Affected** view previews the result: dimmed pixels will not be moved. It opens
  automatically when you move a slider.
- The threshold combines with the mask: the sorted area is the intersection of both.
- The threshold is evaluated on the image at the time of each pass, so in a chain of passes
  every pass has its own area.
- Gray pixels have no hue, so their hue is 0, the same as red.

## Mask

Open the **Mask** view (key `M`): the left mouse button paints, the right button (or `Shift` + left)
erases. The brush size is set with the slider, and **Clear** and **Invert** act on the whole mask.
**Protect selection** excludes the painted area from sorting, and **Sort selection only** does the opposite.

## Algorithms

| Algorithm  | Behavior                                                                  |
|------------|---------------------------------------------------------------------------|
| Odd-even   | Alternating comparisons of even and odd pairs. The default.               |
| Bitonic    | A sorting network with O(log² n) stages, so the fewest animation steps.   |
| Bubble     | One pass per frame. The slowest, but the most spectacular.                |

All three end with the same result. Only the animation differs.

## Keyboard shortcuts

| Key      | Action                |
|----------|-----------------------|
| `Space`  | Play / pause          |
| `O`      | Choose an image       |
| `M`      | Mask view             |
| `R`      | Back to the original  |
| `Ctrl+Z` | Undo the last pass    |

## Project structure

| File               | Contents                                                                  |
|--------------------|---------------------------------------------------------------------------|
| `main.py`          | Entry point. Checks that Tkinter is installed.                            |
| `ui.py`            | The window: panels, preview, mask, animation, history, export.            |
| `sorter.py`        | The engine: segments along a direction, sort keys, threshold, animator.   |
| `mask.py`          | Brush strokes and the preview overlay.                                    |
| `theme.py`         | Colors, fonts and the custom button.                                      |
| `requirements.txt` | Python dependencies (`numpy`, `Pillow`).                                  |

## How it works

1. Pixels are grouped into lines parallel to the chosen direction. After applying the mask and
   the threshold, every line is split into contiguous segments.
2. Every pixel gets a unique key: the property (optionally quantized), then the pixel index on a
   tie. This makes the animation and the fast result identical.
3. The `Animator` runs the chosen algorithm step by step on all segments at once. All comparators
   are ascending, so the padding of shorter segments never moves and every intermediate state can be drawn.
4. The preview and the animation run on a working copy (at most 640 px). The full-resolution result
   is produced by replaying the committed passes on the original (`run_fast`).

## Troubleshooting

- `ModuleNotFoundError: No module named 'tkinter'`: install Tkinter (see Requirements).
- At a high speed with the bubble algorithm the window may stop responding for a moment, because
  the computation runs in the main thread. Lower the speed.

## Limitations

- **Save GIF** records the animation of the next pass from the current state, on the working copy.
