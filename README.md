# Pixel Sort

A small desktop app (Python + Tkinter) that sorts the pixels of a photo along a chosen
direction and animates every step. You can limit the effect with a brush mask and a threshold.

## Features

- Sort along any angle by brightness, hue, saturation or value
- Odd-even, bitonic and bubble sort, animated step by step
- Threshold range and brush mask to control which pixels are sorted
- Chain several passes, undo, export to PNG or GIF

## Setup

Tkinter is a system package, so pip cannot install it.

```bash
sudo apt install python3-tk python3-venv    # Debian / Ubuntu / Linux Mint
git clone https://github.com/omroz14/Pixel-sorter.git
cd Pixel-sorter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

On Windows, Tkinter comes with the python.org installer, and the venv is activated with
`.venv\Scripts\Activate.ps1`.

## Usage

Press `O` to open an image, pick a direction and a sort key, then `Space` to play. Paint areas
in the Mask view (`M`), and use the Threshold sliders to sort only a range of the key.
Save result and Save GIF are in the right panel.
