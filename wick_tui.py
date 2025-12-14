#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///

"""
curses-based TUI for creating Wick contractions and previewing
the corresponding LaTeX snippet (compatible with the simpler-wick package).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import curses
import os
import shutil
import subprocess
import tempfile
import threading


ROW_CONFIG: Sequence[Tuple[str, str, str, str]] = (
    ("inputs", "Input States", "I", r"\phi_{I_{%d}}"),
    ("interactions", "Interaction Terms", "V", r"\mathcal{O}_{%d}"),
    ("externals", "External States", "E", r"\phi_{E_{%d}}"),
)

GRUVBOX_RGB = (
    ("red", (204, 36, 29)),
    ("orange", (214, 93, 14)),
    ("yellow", (215, 153, 33)),
    ("green", (152, 151, 26)),
    ("teal", (104, 157, 106)),
    ("blue", (69, 133, 136)),
    ("purple", (177, 98, 134)),
)

FALLBACK_COLORS = (
    curses.COLOR_RED,
    curses.COLOR_YELLOW,
    curses.COLOR_GREEN,
    curses.COLOR_CYAN,
    curses.COLOR_BLUE,
    curses.COLOR_MAGENTA,
)

VERTICAL_CHAR = "|"
HORIZONTAL_CHAR = "-"
CORNER_CHAR = "+"

class PreviewError(Exception):
    """Raised when generating a preview image fails."""


class PreviewManager:
    """Handles temp file creation and LaTeX/PDF/PNG generation for previews."""
    def __init__(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="wick_preview_"))
        self.base_name = "preview"

    @property
    def tex_path(self) -> Path:
        return self.tempdir / f"{self.base_name}.tex"

    @property
    def pdf_path(self) -> Path:
        return self.tempdir / f"{self.base_name}.pdf"

    @property
    def image_prefix(self) -> Path:
        return self.tempdir / f"{self.base_name}_image"

    @property
    def image_path(self) -> Path:
        return self.image_prefix.with_suffix(".png")

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.tempdir)
        except OSError:
            pass

    def write_tex(self, snippet: str) -> Path:
        tex_body = r"""\documentclass[14pt]{article}
\usepackage[paperwidth=4in,paperheight=1.5in,margin=0.1in]{geometry}
\usepackage{simpler-wick}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{braket}
\usepackage{graphicx}
\begin{document}
\thispagestyle{empty}
\[
\scalebox{1.9}{$
%s
$}
\]
\end{document}
""" % snippet
        self.tex_path.write_text(tex_body, encoding="utf-8")
        return self.tex_path

    def build_preview_image(self, snippet: str) -> Path:
        self.write_tex(snippet)
        latex_cmd = [
            "latexmk",
            "-pdflua",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-quiet",
            self.tex_path.name,
        ]
        latex_proc = subprocess.run(
            latex_cmd,
            cwd=self.tempdir,
            capture_output=True,
            text=True,
        )
        if latex_proc.returncode != 0:
            raise PreviewError(
                "LaTeX compilation failed (see log in temp directory). "
                f"stderr: {latex_proc.stderr.strip()[:200]}"
            )
        pdf_path = self.pdf_path
        if not pdf_path.exists():
            raise PreviewError("Expected preview PDF was not generated.")
        image_prefix = self.image_prefix
        convert_cmd = [
            "pdftoppm",
            "-png",
            "-singlefile",
            "-f",
            "1",
            "-l",
            "1",
            str(pdf_path),
            str(image_prefix),
        ]
        convert_proc = subprocess.run(
            convert_cmd,
            cwd=self.tempdir,
            capture_output=True,
            text=True,
        )
        if convert_proc.returncode != 0:
            raise PreviewError(
                "Failed converting PDF to PNG. "
                f"stderr: {convert_proc.stderr.strip()[:200]}"
            )
        image_path = self.image_path
        if not image_path.exists():
            raise PreviewError("Preview image missing after conversion.")
        return image_path

def set_cursor_visible(visible: bool) -> None:
    try:
        curses.curs_set(1 if visible else 0)
    except curses.error:
        pass


@dataclass
class Node:
    node_id: str
    kind: str
    index: int
    latex_symbol: str
    x: int = 0
    y: int = 0


@dataclass
class PreviewState:
    image_path: Optional[Path] = None
    visible: bool = False
    needs_refresh: bool = False
    dims: Tuple[int, int, int] = (0, 0, 0)

    def update_dims(self, dims: Tuple[int, int, int]) -> None:
        if dims != self.dims:
            self.dims = dims
            if self.visible:
                self.needs_refresh = True


class PreviewJobController:
    """Runs preview builds off the UI thread and surfaces the latest result."""
    def __init__(self, manager: PreviewManager) -> None:
        self._manager = manager
        self._lock = threading.Lock()
        self._running = False
        self._pending_snippet: Optional[str] = None
        self._result: Optional[Tuple[Optional[Path], str]] = None

    def request(self, snippet: str) -> None:
        with self._lock:
            if self._running:
                self._pending_snippet = snippet
                return
            self._running = True
        self._start_thread(snippet)

    def _start_thread(self, snippet: str) -> None:
        def worker() -> None:
            image_path, message = generate_preview_image(snippet, self._manager)
            self._finish(image_path, message)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _finish(self, image_path: Optional[Path], message: str) -> None:
        with self._lock:
            self._result = (image_path, message)
            self._running = False
            next_snippet = self._pending_snippet
            self._pending_snippet = None
        if next_snippet is not None:
            self.request(next_snippet)

    def fetch_result(self) -> Optional[Tuple[Optional[Path], str]]:
        with self._lock:
            result = self._result
            self._result = None
        return result


def init_gruvbox_pairs() -> List[int]:
    """Initialize a palette of foreground-only color pairs."""
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    color_attrs: List[int] = []
    next_color_id = 16
    for idx, (_name, (r, g, b)) in enumerate(GRUVBOX_RGB):
        color_id: Optional[int] = None
        if curses.can_change_color() and next_color_id < curses.COLORS:
            try:
                curses.init_color(
                    next_color_id,
                    int(r / 255 * 1000),
                    int(g / 255 * 1000),
                    int(b / 255 * 1000),
                )
                color_id = next_color_id
                next_color_id += 1
            except curses.error:
                color_id = None
        if color_id is None:
            fallback = FALLBACK_COLORS[idx % len(FALLBACK_COLORS)]
            color_id = fallback
        pair_id = 10 + idx
        if pair_id >= curses.COLOR_PAIRS:
            break
        try:
            curses.init_pair(pair_id, color_id, -1)
            color_attrs.append(curses.color_pair(pair_id))
        except curses.error:
            continue
    if not color_attrs:
        color_attrs.append(curses.A_BOLD)
    return color_attrs


def parse_args() -> argparse.Namespace:
    """CLI arguments for initial operator counts."""
    parser = argparse.ArgumentParser(
        description="Terminal UI for drawing Wick contractions.",
    )
    parser.add_argument(
        "--inputs", type=int, default=2, help="Number of input state operators (default: 2)"
    )
    parser.add_argument(
        "--interactions",
        type=int,
        default=6,
        help="Number of interaction terms (default: 6)",
    )
    parser.add_argument(
        "--externals",
        type=int,
        default=2,
        help="Number of external states (default: 2)",
    )
    return parser.parse_args()


def build_nodes(counts: Dict[str, int], previous_nodes: Optional[Sequence[Node]] = None) -> List[Node]:
    """Create nodes in order, preserving any existing latex_symbol values by index."""
    previous_lookup: Dict[Tuple[str, int], str] = {}
    if previous_nodes:
        for node in previous_nodes:
            previous_lookup[(node.kind, node.index)] = node.latex_symbol
    nodes: List[Node] = []
    for key, _name, _prefix, _latex_tpl in ROW_CONFIG:
        amount = max(0, counts.get(key, 0))
        for idx in range(amount):
            existing_value = previous_lookup.get((key, idx + 1), "")
            nodes.append(
                Node(
                    node_id=f"{key}-{idx}",
                    kind=key,
                    index=idx + 1,
                    latex_symbol=existing_value,
                )
            )
    return nodes


def layout_nodes_inline(nodes: Sequence[Node], width: int, height: int) -> int:
    """Place all nodes on a single horizontal line based on terminal width."""
    if not nodes:
        return max(3, height // 2)
    margin_x = 4
    available_width = max(10, width - 2 * margin_x)
    base_y = max(4, height // 2)
    for idx, node in enumerate(nodes):
        center = margin_x + int(((idx + 0.5) / len(nodes)) * available_width)
        text = render_node_display(node)
        node.x = max(1, center - len(text) // 2)
        node.y = base_y
    return base_y


def render_node_display(node: Node) -> str:
    content = node.latex_symbol if node.latex_symbol else " "
    return f"[{content}]"


def safe_addch(
    stdscr: "curses._CursesWindow", y: int, x: int, ch: str, attr: int = curses.A_NORMAL
) -> None:
    height, width = stdscr.getmaxyx()
    if not (0 <= y < height and 0 <= x < width):
        return
    try:
        stdscr.addch(y, x, ch, attr)
    except curses.error:
        pass


def node_center(node: Node) -> int:
    return node.x + len(render_node_display(node)) // 2


def describe_node(node: Node) -> str:
    for key, row_name, _, _ in ROW_CONFIG:
        if key == node.kind:
            return f"{row_name} #{node.index}"
    return f"{node.kind} #{node.index}"


def draw_vertical_up(
    stdscr: "curses._CursesWindow",
    x: int,
    base_y: int,
    top_y: int,
    attr: int,
) -> None:
    if top_y >= base_y:
        return
    for y in range(base_y - 1, top_y - 1, -1):
        safe_addch(stdscr, y, x, VERTICAL_CHAR, attr)


def draw_horizontal_segment(
    stdscr: "curses._CursesWindow", start_x: int, end_x: int, y: int, attr: int
) -> None:
    if start_x == end_x:
        safe_addch(stdscr, y, start_x, HORIZONTAL_CHAR, attr)
        return
    left, right = sorted((start_x, end_x))
    safe_addch(stdscr, y, left, CORNER_CHAR, attr)
    safe_addch(stdscr, y, right, CORNER_CHAR, attr)
    for x in range(left + 1, right):
        safe_addch(stdscr, y, x, HORIZONTAL_CHAR, attr)


def generate_latex(nodes: Sequence[Node], connections: Sequence[Tuple[str, str]]) -> str:
    r"""Build a \wick snippet grouped into a \braket with | separators."""
    if not nodes:
        return ""
    index_lookup = {node.node_id: idx for idx, node in enumerate(nodes)}
    decorated = [node.latex_symbol or "" for node in nodes]

    for contraction_id, pair in enumerate(connections, 1):
        node_a, node_b = pair
        if node_a not in index_lookup or node_b not in index_lookup:
            continue
        idx_a = index_lookup[node_a]
        idx_b = index_lookup[node_b]
        label = (contraction_id - 1) % 9 + 1
        for idx in sorted((idx_a, idx_b)):
            decorated[idx] = rf"\c{label}{{{decorated[idx]}}}"
    kind_to_tokens: Dict[str, List[str]] = {key: [] for key, *_ in ROW_CONFIG}
    for node in nodes:
        kind_to_tokens[node.kind].append(decorated[index_lookup[node.node_id]])
    segments: List[str] = []
    for key, _label, _prefix, _tpl in ROW_CONFIG:
        tokens = kind_to_tokens[key]
        if not tokens:
            continue
        segment_tokens = [tok if tok.strip() else " " for tok in tokens]
        segments.append(" ".join(segment_tokens))
    if not segments:
        inner = " "
    else:
        inner = " | ".join(segments)
    return rf"\braket{{ \wick{{ {inner} }} }}"


def refresh_status_bar(stdscr: "curses._CursesWindow", text: str) -> None:
    height, width = stdscr.getmaxyx()
    msg = f"Status: {text}"
    stdscr.addstr(height - 4, 2, msg[: width - 4])
    stdscr.clrtoeol()


def prompt_for_counts(
    stdscr: "curses._CursesWindow", counts: Dict[str, int]
) -> Optional[Dict[str, int]]:
    curses.echo()
    set_cursor_visible(True)
    height, width = stdscr.getmaxyx()
    new_counts: Dict[str, int] = {}
    try:
        for key, row_name, _, _ in ROW_CONFIG:
            while True:
                prompt = f"{row_name} ({counts[key]}): "
                stdscr.addstr(height - 3, 2, " " * (width - 4))
                stdscr.addstr(height - 3, 2, prompt[: width - 4])
                stdscr.refresh()
                try:
                    raw = stdscr.getstr(height - 3, 2 + len(prompt), 6)
                except curses.error:
                    continue
                if raw is None:
                    continue
                text = raw.decode().strip()
                if not text:
                    new_counts[key] = counts[key]
                    break
                if text.isdigit():
                    new_counts[key] = int(text)
                    break
            stdscr.addstr(height - 3, 2, " " * (width - 4))
            stdscr.refresh()
    finally:
        curses.noecho()
        set_cursor_visible(False)
    if sum(new_counts.values()) == 0:
        return None
    return new_counts


def edit_node_value(stdscr: "curses._CursesWindow", node: Node) -> bool:
    """Inline editor for a node's LaTeX contents."""
    curses.echo()
    set_cursor_visible(True)
    height, width = stdscr.getmaxyx()
    changed = False
    try:
        stdscr.addstr(height - 4, 2, " " * (width - 4))
        stdscr.addstr(height - 4, 2, f"{describe_node(node)} value (blank keeps current): "[: width - 4])
        value_prompt = "New contents: "
        stdscr.addstr(height - 3, 2, " " * (width - 4))
        stdscr.addstr(height - 3, 2, value_prompt[: width - 4])
        stdscr.refresh()
        max_len = max(4, width - len(value_prompt) - 4)
        raw_value = stdscr.getstr(height - 3, 2 + len(value_prompt), max_len)
        if raw_value:
            text = raw_value.decode().strip()
            if text:
                node.latex_symbol = text
                changed = True
    finally:
        curses.noecho()
        set_cursor_visible(False)
        stdscr.addstr(height - 4, 2, " " * (width - 4))
        stdscr.addstr(height - 3, 2, " " * (width - 4))
    return changed


def copy_to_clipboard(text: str) -> Tuple[bool, str]:
    """Try common clipboard tools to copy the provided text."""
    if not text.strip():
        return False, "Nothing to copy."
    commands: List[List[str]] = []
    if shutil.which("pbcopy"):
        commands.append(["pbcopy"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        commands.append(["xsel", "--clipboard", "--input"])
    if shutil.which("clip.exe"):
        commands.append(["clip.exe"])
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, input=text, text=True, capture_output=True)
            if proc.returncode == 0:
                return True, "LaTeX snippet copied to clipboard."
        except (OSError, subprocess.SubprocessError):
            continue
    return False, "No clipboard tool found (tried pbcopy/xclip/xsel/clip.exe)."


def kitty_inline_available() -> bool:
    """Return True if running inside kitty with the executable available."""
    return bool(os.environ.get("KITTY_WINDOW_ID")) and shutil.which("kitty") is not None


def clear_inline_preview() -> None:
    """Clear any inline kitty image, ignoring failures."""
    if not kitty_inline_available():
        return
    try:
        subprocess.run(
            ["kitty", "+kitten", "icat", "--clear"],
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


def show_inline_preview(image_path: Path, x: int, y: int, width: int, height: int) -> Tuple[bool, str]:
    """Place the preview image inline within the kitty terminal region."""
    if height <= 0 or width <= 0:
        return False, "Not enough space to display the preview."
    cmd = [
        "kitty",
        "+kitten",
        "icat",
        "--transfer-mode=file",
        f"--place={width}x{height}@{x}x{y}",
        str(image_path),
    ]
    try:
        clear_inline_preview()
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        return False, "kitty executable not found."
    except subprocess.CalledProcessError as exc:
        return False, f"Kitty preview failed (exit {exc.returncode})."
    return True, ""


def generate_preview_image(
    latex_snippet: str,
    preview_manager: PreviewManager,
) -> Tuple[Optional[Path], str]:
    """Compile LaTeX to PDF then PNG; return the image path or an error message."""
    if not latex_snippet.strip():
        return None, "Add operators before generating a preview."
    if not kitty_inline_available():
        return None, "Kitty inline preview is only available inside the kitty terminal."
    missing_cmds = [cmd for cmd in ("latexmk", "pdftoppm") if shutil.which(cmd) is None]
    if missing_cmds:
        return None, f"Missing commands for preview: {', '.join(missing_cmds)}."
    try:
        image_path = preview_manager.build_preview_image(latex_snippet)
    except PreviewError as exc:
        return None, str(exc)
    return image_path, "Preview updated."


def hide_preview(preview_state: PreviewState) -> None:
    if preview_state.visible:
        clear_inline_preview()
    preview_state.visible = False
    preview_state.needs_refresh = False


def process_preview_display(preview_state: PreviewState) -> Optional[str]:
    """Render inline preview if needed; return a status message when applicable."""
    if not preview_state.visible or not preview_state.image_path:
        return None
    top, height_cells, width_cells = preview_state.dims
    if height_cells <= 0 or width_cells <= 0:
        hide_preview(preview_state)
        return "Terminal too small to show the preview."
    if not preview_state.needs_refresh:
        return None
    success, message = show_inline_preview(
        preview_state.image_path, 2, top, width_cells, height_cells
    )
    if not success:
        hide_preview(preview_state)
        return message
    preview_state.needs_refresh = False
    return None


def draw_connections_right_angles(
    stdscr: "curses._CursesWindow",
    nodes_lookup: Dict[str, Node],
    connections: Sequence[Tuple[str, str]],
    base_y: int,
    color_attrs: Sequence[int],
) -> None:
    if base_y <= 1 or not connections:
        return
    max_span = max(1, base_y - 2)
    max_levels = max(1, max_span // 2)
    for conn_index, (node_a_id, node_b_id) in enumerate(connections):
        node_a = nodes_lookup.get(node_a_id)
        node_b = nodes_lookup.get(node_b_id)
        if not node_a or not node_b:
            continue
        left_node, right_node = (node_a, node_b)
        if node_center(left_node) > node_center(right_node):
            left_node, right_node = right_node, left_node
        start_x = node_center(left_node)
        end_x = node_center(right_node)
        if start_x == end_x:
            continue
        level = conn_index % max_levels
        top_y = max(1, base_y - 2 - level * 2)
        attr = color_attrs[conn_index % len(color_attrs)] if color_attrs else curses.A_NORMAL
        draw_vertical_up(stdscr, start_x, base_y, top_y, attr)
        draw_vertical_up(stdscr, end_x, base_y, top_y, attr)
        draw_horizontal_segment(stdscr, start_x, end_x, top_y, attr)


def draw_nodes(
    stdscr: "curses._CursesWindow",
    nodes: Sequence[Node],
    selected_index: int,
    pending_node: Optional[str],
    node_to_connection: Dict[str, int],
) -> None:
    _, width = stdscr.getmaxyx()
    for idx, node in enumerate(nodes):
        attr = curses.A_NORMAL
        if idx == selected_index:
            attr |= curses.A_REVERSE
        if pending_node == node.node_id:
            attr |= curses.A_BOLD
        if node.node_id in node_to_connection:
            attr |= curses.A_DIM
        text = render_node_display(node)
        try:
            stdscr.addstr(node.y, node.x, text[: max(0, width - node.x - 1)], attr)
        except curses.error:
            continue


def draw_latex_preview(stdscr: "curses._CursesWindow", latex: str) -> int:
    height, width = stdscr.getmaxyx()
    preview = f"LaTeX: {latex}" if latex else "LaTeX: (add fields to start)"
    lines: List[str] = []
    while preview:
        lines.append(preview[: width - 4])
        preview = preview[width - 4 :]
    start_row = height - (len(lines) + 2)
    start_row = max(2, start_row)
    for offset, line in enumerate(lines):
        stdscr.addstr(start_row + offset, 2, line)
    return start_row


def move_selection(nodes: Sequence[Node], index: int, direction: str) -> int:
    if not nodes:
        return index
    if direction in ("left", "up"):
        return max(0, index - 1)
    if direction in ("right", "down"):
        return min(len(nodes) - 1, index + 1)
    return index


def rebuild_lookup(nodes: Sequence[Node], connections: Sequence[Tuple[str, str]]) -> Dict[str, int]:
    lookup: Dict[str, int] = {}
    for idx, (node_a, node_b) in enumerate(connections):
        lookup[node_a] = idx
        lookup[node_b] = idx
    return lookup


def run_tui(counts: Dict[str, int]) -> Tuple[List[Tuple[str, str]], str]:
    """Main event loop wrapper; returns final contractions and LaTeX snippet."""
    preview_manager = PreviewManager()
    preview_state = PreviewState()
    preview_job = PreviewJobController(preview_manager)
    nodes = build_nodes(counts)
    if not nodes:
        raise ValueError("At least one operator is required to start the interface.")

    connections: List[Tuple[str, str]] = []
    node_to_connection: Dict[str, int] = {}
    selected_index = 0
    pending_node: Optional[str] = None
    status_message = "Use arrows to move, Enter to start a contraction."
    connection_colors: List[int] = []
    clipboard_content = ""

    def curses_main(stdscr: "curses._CursesWindow") -> None:
        nonlocal nodes, connections, node_to_connection, connection_colors, preview_manager
        nonlocal selected_index, pending_node, status_message
        nonlocal preview_state, clipboard_content
        set_cursor_visible(False)
        stdscr.nodelay(False)
        stdscr.keypad(True)
        connection_colors = init_gruvbox_pairs()
        while True:
            height, width = stdscr.getmaxyx()
            stdscr.erase()
            base_y = layout_nodes_inline(nodes, width, height)
            node_lookup = {node.node_id: node for node in nodes}
            draw_connections_right_angles(
                stdscr, node_lookup, connections, base_y, connection_colors
            )
            draw_nodes(stdscr, nodes, selected_index, pending_node, node_to_connection)
            latex_snippet = generate_latex(nodes, connections)
            latex_top_row = draw_latex_preview(stdscr, latex_snippet)
            preview_top = max(base_y + 4, 4)
            preview_bottom = max(preview_top, latex_top_row - 2)
            preview_height = max(0, preview_bottom - preview_top)
            preview_width = max(10, width - 4)
            preview_state.update_dims((preview_top, preview_height, preview_width))
            instructions = (
                "Arrows: move  Enter: pair  i: edit  y: yank  p: paste  c: copy LaTeX  v: preview (kitty)  d: clear  r: resize counts  q: quit"
            )
            stdscr.addstr(1, 2, instructions[: width - 4])
            counts_text = " / ".join(f"{row_name}: {counts[key]}" for key, row_name, _, _ in ROW_CONFIG)
            stdscr.addstr(2, 2, counts_text[: width - 4])
            refresh_status_bar(stdscr, status_message)
            stdscr.refresh()
            job_result = preview_job.fetch_result()
            if job_result:
                image_path, message = job_result
                status_message = message
                if image_path:
                    preview_state.image_path = image_path
                    preview_state.visible = True
                    preview_state.needs_refresh = True
                else:
                    hide_preview(preview_state)
            preview_message = process_preview_display(preview_state)
            if preview_message:
                status_message = preview_message

            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                continue
            if key in (ord("q"), ord("Q")):
                break
            if key in (curses.KEY_LEFT, ord("h")):
                selected_index = move_selection(nodes, selected_index, "left")
            elif key in (curses.KEY_RIGHT, ord("l")):
                selected_index = move_selection(nodes, selected_index, "right")
            elif key in (curses.KEY_UP, ord("k")):
                selected_index = move_selection(nodes, selected_index, "up")
            elif key in (curses.KEY_DOWN, ord("j")):
                selected_index = move_selection(nodes, selected_index, "down")
            elif key in (ord("i"), ord("I")):
                if not nodes:
                    continue
                current = nodes[selected_index]
                changed = edit_node_value(stdscr, current)
                desc = describe_node(current)
                status_message = f"Updated {desc}." if changed else f"Kept previous {desc.lower()}."
            elif key in (ord("y"), ord("Y")):
                if not nodes:
                    continue
                clipboard_content = nodes[selected_index].latex_symbol
                status_message = f"Copied {describe_node(nodes[selected_index])}."
            elif key in (ord("p"), ord("P")):
                if not nodes:
                    continue
                if clipboard_content == "":
                    status_message = "Clipboard is empty; copy a field first."
                else:
                    nodes[selected_index].latex_symbol = clipboard_content
                    status_message = f"Pasted into {describe_node(nodes[selected_index])}."
            elif key in (ord("v"), ord("V")):
                preview_job.request(latex_snippet)
                status_message = "Rendering preview..."
            elif key in (ord("c"), ord("C")):
                success, message = copy_to_clipboard(latex_snippet)
                status_message = message
            elif key in (curses.KEY_ENTER, ord("\n"), ord("\r"), ord(" ")):
                if not nodes:
                    continue
                current = nodes[selected_index]
                conn_idx = node_to_connection.get(current.node_id)
                if conn_idx is not None:
                    status_message = f"{describe_node(current)} already participates in a contraction."
                    continue
                if pending_node is None:
                    pending_node = current.node_id
                    status_message = f"Selected {describe_node(current)}; pick a partner."
                else:
                    if pending_node == current.node_id:
                        pending_node = None
                        status_message = "Canceled pending selection."
                        continue
                    connections.append((pending_node, current.node_id))
                    node_to_connection = rebuild_lookup(nodes, connections)
                    pending_node = None
                    status_message = "Added contraction."
            elif key in (curses.KEY_BACKSPACE, 127, curses.KEY_DC):
                current = nodes[selected_index]
                idx = node_to_connection.get(current.node_id)
                if idx is None:
                    status_message = f"No contraction to remove for {describe_node(current)}."
                else:
                    del connections[idx]
                    node_to_connection = rebuild_lookup(nodes, connections)
                    pending_node = None
                    status_message = "Removed contraction."
            elif key in (ord("d"), ord("D")):
                connections.clear()
                node_to_connection = {}
                pending_node = None
                status_message = "Cleared all contractions."
            elif key in (ord("r"), ord("R")):
                new_counts = prompt_for_counts(stdscr, counts)
                if not new_counts:
                    status_message = "Need at least one operator."
                    continue
                counts.update(new_counts)
                nodes = build_nodes(counts, nodes)
                connections.clear()
                node_to_connection = {}
                pending_node = None
                selected_index = 0
                status_message = "Rebuilt layout with new counts."

    try:
        curses.wrapper(curses_main)
    finally:
        clear_inline_preview()
        preview_manager.cleanup()
    latex = generate_latex(nodes, connections)
    return connections, latex


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    counts = {"inputs": args.inputs, "interactions": args.interactions, "externals": args.externals}
    try:
        connections, latex_snippet = run_tui(counts)
    except ValueError as exc:
        print(exc)
        return

    print("\nFinal contractions:")
    if not connections:
        print("  (none)")
    else:
        for idx, pair in enumerate(connections, 1):
            print(f"  {idx}. {pair[0]} -> {pair[1]}")
    print("\nLaTeX snippet:")
    print(latex_snippet or "(no operators configured)")


if __name__ == "__main__":
    main()
