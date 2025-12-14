## Wick Contraction TUI

Interactive curses-based terminal UI for constructing Wick contractions and previewing
the LaTeX snippet compatible with the `simpler-wick` package.

Note that this was made very haphazardly, and was only made for my personal use, it has not been tested on any other machine.

### Requirements

- Python 3.9+ (tested with the system Python)
- A terminal that supports `curses` (most Unix-like environments do)
- For latex preview, a terminal that supports the kitty graphics protocol
### Running

```bash
python3 wick_tui.py --inputs 3 --interactions 6 --externals 2
```
If you omit the flags, it defaults to 2 to 2 scattering with 6 interaction terms. Once the interface
opens you can press `r` to adjust the counts without restarting the TUI (changing
counts resets contractions).

### Controls

- Arrow keys / `h` `j` `k` `l`: Move the selector between operators
- `Enter` or space: Start/end a contraction (operators may only be used once)
- `i`: Edit the LaTeX contents of the selected operator (they start blank)
- `y`: Yank the contents of the selected operator into a clipboard
- `p`: Paste the clipboard contents into the selected operator
- `c`: Copy the current LaTeX snippet to your system clipboard (tries `pbcopy`, `xclip`, `xsel`, or `clip.exe`)
- `v`: Render a live LaTeX preview inside kitty (requires `latexmk`, `pdftoppm`, and kitty)
- `d`: Clear every contraction
- `Backspace`: Remove the contraction that involves the selected operator
- `r`: Reconfigure the number of input/interactions/external operators
- `q`: Quit the UI

All operators appear in a single line (inputs, interactions, externals in order),
and each contraction is drawn as a colored right-angle path above the line using
a Gruvbox-inspired palette so overlapping connections stay readable.

### LaTeX output

The live preview at the bottom of the UI displays the snippet inside a
`\braket{ \wick{ ... } }` so the input, interaction, and external states are
separated by literal `|` characters. Each contracted operator is wrapped with the
matching `\cN{...}` macro (for example `\c1{}` / `\c1{}` pairs). Because every
slot begins empty, use `i` to populate the LaTeX body before or after drawing
contractions. When you quit the UI the final list of contractions and the snippet
are printed to the terminal so you can paste them directly into your document.

### Inline kitty preview (optional)

If you are running the TUI inside the kitty terminal (with the `KITTY_WINDOW_ID`
environment present) and have both `latexmk` and `pdftoppm` installed, press `v`
to compile the current snippet via LuaLaTeX and display the resulting PDF page
inline (just beneath the contraction diagram) using Kitty’s graphics protocol.
Preview generation runs asynchronously, so you can keep editing while LaTeX
compiles; press `v` again whenever you want the preview to refresh. Temporary
files are created under a scratch directory and cleaned up automatically when you
exit the application.

