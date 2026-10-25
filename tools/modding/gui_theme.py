"""gui_theme.py -- the look of clone_gui.py's window.

A dark "night arena" theme for tkinter/ttk in the style of the Project Rio community's tools: near-black
panels with hairline borders, a red accent, uppercase tracked display type (Rajdhani), Inter for body
text, JetBrains Mono for paths and the log.  Everything is drawn with the standard library: ttk
elements use small anti-aliased images painted here (rounded fields, buttons, check boxes), and the
page, panels, chips and the angled primary button are tk.Canvas drawings.

Imported only when the window is built (it imports tkinter; clone_gui's logic must not).
"""
import base64
import math
import struct
import sys
import zlib
from pathlib import Path

import tkinter as tk
from tkinter import ttk, font as tkfont

# ----------------------------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------------------------
PAGE = "#07070b"
PANEL = "#0b0b12"
POP = "#0e0e17"
SURFACE = "#10101a"
LINE = "#1a1a2e"
FIELD = "#08080d"
TEXT = "#f5f5f8"
MUTED = "#8f8fa3"
RED = "#e60012"
FOCUS = "#ff1a2a"
DANGER = "#ef4444"
OK = "#22c55e"
GOLD = "#f5bb00"


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def hexc(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


def mix(a, b, t):
    """Colour b laid over a at opacity t."""
    return hexc(x + (y - x) * t for x, y in zip(rgb(a), rgb(b)))


HOVER = mix(SURFACE, RED, 0.10)
PRESS = mix(SURFACE, RED, 0.20)
RED_LINE = mix(PANEL, RED, 0.40)
SOFT_LINE = mix(LINE, MUTED, 0.30)
DIM = mix(MUTED, PANEL, 0.45)
LOG_TEXT = mix(MUTED, TEXT, 0.35)

S = 1.0          # UI scale (1.0 at 96 dpi); set by init()


def px(n):
    return int(round(n * S))


# ----------------------------------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------------------------------
FONT_FILES = ("Rajdhani-SemiBold.ttf", "Rajdhani-Bold.ttf", "Inter-Regular.ttf", "Inter-Medium.ttf",
              "Inter-SemiBold.ttf", "JetBrainsMono-Regular.ttf")


def font_dir():
    base = getattr(sys, "_MEIPASS", None)          # PyInstaller one-file: unpacked next to the code
    return (Path(base) if base else Path(__file__).resolve().parent) / "fonts"


def load_private_fonts():
    """Windows: make the bundled fonts usable by this process only (nothing is installed).
    Must run before tk.Tk().  Returns how many were loaded; elsewhere fonts come from the system."""
    if sys.platform != "win32":
        return 0
    loaded = 0
    try:
        import ctypes
        add = ctypes.windll.gdi32.AddFontResourceExW
        for name in FONT_FILES:
            p = font_dir() / name
            if p.is_file() and add(str(p), 0x10, 0):   # FR_PRIVATE
                loaded += 1
    except Exception:
        pass
    return loaded


# role: ([(family, weight), ...] in order of preference, size in px at 96 dpi)
FONT_ROLES = {
    # Rajdhani is narrow with a small x-height: its stand-ins are set smaller (third item)
    "title":   ([("Rajdhani SemiBold", "normal"), ("Segoe UI Semibold", "normal", .8), ("Segoe UI", "bold", .8)], 21),
    "head":    ([("Rajdhani SemiBold", "normal"), ("Segoe UI Semibold", "normal", .82), ("Segoe UI", "bold", .82)], 16),
    "button":  ([("Rajdhani", "bold"), ("Segoe UI", "bold", .82)], 16),
    "body":    ([("Inter", "normal"), ("Segoe UI", "normal")], 13),
    "medium":  ([("Inter Medium", "normal"), ("Segoe UI Semibold", "normal"), ("Segoe UI", "normal")], 13),
    "small":   ([("Inter", "normal"), ("Segoe UI", "normal")], 12),
    "label":   ([("Inter SemiBold", "normal"), ("Segoe UI Semibold", "normal"), ("Segoe UI", "bold")], 10.5),
    "kicker":  ([("Inter SemiBold", "normal"), ("Segoe UI Semibold", "normal"), ("Segoe UI", "bold")], 10),
    "chip":    ([("Inter SemiBold", "normal"), ("Segoe UI Semibold", "normal"), ("Segoe UI", "bold")], 9.5),
    "mono":    ([("JetBrains Mono", "normal"), ("Consolas", "normal"), ("DejaVu Sans Mono", "normal")], 12),
    "icon":    ([("Inter", "normal"), ("Segoe UI", "normal")], 16),
}
F = {}            # role -> tkfont.Font
FONT_FALLBACK = []  # roles that did not get their first choice


def _family_ok(root, family):
    """True if Tk really gives us this family (not a substitute)."""
    actual = tkfont.Font(root=root, family=family, size=-12).actual("family").lower()
    return actual.split()[0] == family.lower().split()[0]


def _make_fonts(root):
    seen = {}
    for role, (choices, size) in FONT_ROLES.items():
        pick = None
        for choice in choices:
            fam = choice[0]
            if fam not in seen:
                seen[fam] = _family_ok(root, fam)
            if seen[fam]:
                pick = choice
                break
        if pick is None:
            base = tkfont.nametofont("TkDefaultFont").actual()
            pick = (base["family"],) + choices[-1][1:]
        if pick is not choices[0]:
            FONT_FALLBACK.append(role)
        scale = pick[2] if len(pick) > 2 else 1.0
        F[role] = tkfont.Font(root=root, family=pick[0], size=-px(size * scale), weight=pick[1])


# ----------------------------------------------------------------------------------------------
# Tiny anti-aliased painter (signed distance shapes -> RGBA PNG -> PhotoImage)
# ----------------------------------------------------------------------------------------------
def _png_rgba(w, h, data):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    stride = w * 4
    raw = b"".join(b"\0" + bytes(data[y * stride:(y + 1) * stride]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def sd_box(x0, y0, x1, y1, r):
    cx, cy, bx, by = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2

    def f(x, y):
        qx, qy = abs(x - cx) - bx + r, abs(y - cy) - by + r
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return f


def sd_circle(cx, cy, r):
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def sd_polyline(pts, w):
    segs = list(zip(pts, pts[1:]))

    def f(x, y):
        best = 1e9
        for (ax, ay), (bx, by) in segs:
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
        return best - w / 2
    return f


def sd_convex(pts):
    """Convex polygon (clockwise in screen coords); good enough for anti-aliased edges."""
    edges = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        nx, ny = by - ay, -(bx - ax)
        ln = math.hypot(nx, ny)
        edges.append((ax, ay, nx / ln, ny / ln))
    return lambda x, y: max((x - ax) * nx + (y - ay) * ny for ax, ay, nx, ny in edges)


def paint(w, h, layers, bg=PANEL):
    """layers: [(sdf, colour, opacity)] composited in order over `bg` (opaque), or over transparent
    when bg is None.  -> PhotoImage.  Opaque images matter for speed: Tk blends partly transparent
    photo pixels in software on every redraw (reading the window back on X11), which made each
    relayout of the page cost 70-230 ms; every themed widget sits on a known colour anyway."""
    buf = bytearray(w * h * 4)
    cols = [(f, rgb(c), a) for f, c, a in layers]
    base = rgb(bg) if bg is not None else None
    for y in range(h):
        for x in range(w):
            if base is None:
                r = g = b = a = 0.0
            else:
                (r, g, b), a = base, 1.0
            for f, (cr, cg, cb), op in cols:
                cov = 0.5 - f(x + 0.5, y + 0.5)
                if cov <= 0:
                    continue
                k = min(cov, 1.0) * op
                na = k + a * (1 - k)
                if na > 0:
                    r = (cr * k + r * a * (1 - k)) / na
                    g = (cg * k + g * a * (1 - k)) / na
                    b = (cb * k + b * a * (1 - k)) / na
                a = na
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((int(r + .5), int(g + .5), int(b + .5), int(a * 255 + .5)))
    return tk.PhotoImage(data=base64.b64encode(_png_rgba(w, h, buf)).decode("ascii"))


def rounded(w, h, r, fill, border=None, bw=1.0, bg=PANEL):
    layers = []
    if border:
        layers.append((sd_box(0, 0, w, h, r), border, 1.0))
        layers.append((sd_box(bw, bw, w - bw, h - bw, max(r - bw, 0)), fill, 1.0))
    else:
        layers.append((sd_box(0, 0, w, h, r), fill, 1.0))
    return paint(w, h, layers, bg)


def widen(img, b, w=240, h=64):
    """A 9-slice image (corners b x b) re-laid to w x h with Tk's own image copy (tiling the edges
    and the middle).  ttk draws an "image" element by tiling its stretchable middle: from a
    17-pixel image that is hundreds of copies for every combobox redraw (~30 ms each); from a
    widget-sized one it is a handful."""
    n = img.width()
    out = tk.PhotoImage(width=w, height=h)
    m = n - b

    def cp(fx0, fy0, fx1, fy1, tx0, ty0, tx1, ty1):
        out.tk.call(out, "copy", img, "-from", fx0, fy0, fx1, fy1, "-to", tx0, ty0, tx1, ty1)
    cp(0, 0, b, b, 0, 0, b, b)
    cp(m, 0, n, b, w - b, 0, w, b)
    cp(0, m, b, n, 0, h - b, b, h)
    cp(m, m, n, n, w - b, h - b, w, h)
    cp(b, 0, m, b, b, 0, w - b, b)
    cp(b, m, m, n, b, h - b, w - b, h)
    cp(0, b, b, m, 0, b, b, h - b)
    cp(m, b, n, m, w - b, b, w, h - b)
    cp(b, b, m, m, b, b, w - b, h - b)
    return out


def mark_image(size, bg=None):
    """The app mark: a white baseball with red seams on a red tile (transparent corners for the
    window icon, or on `bg`)."""
    s = size / 32.0
    ball = sd_circle(16 * s, 16 * s, 11 * s)
    tile = sd_box(0, 0, size, size, 7 * s)

    def seam(cx):
        ring = sd_circle(cx * s, 16 * s, 10.5 * s)
        return lambda x, y: max(abs(ring(x, y)) - 1.1 * s, ball(x, y))
    return paint(size, size, [(tile, RED, 1.0), (ball, TEXT, 1.0), (seam(1.5), RED, 1.0), (seam(30.5), RED, 1.0)], bg)


# ----------------------------------------------------------------------------------------------
# ttk theme ("night", built on clam)
# ----------------------------------------------------------------------------------------------
_IMAGES = []      # keep PhotoImages alive


def _keep(img):
    _IMAGES.append(img)
    return img


def _build_style(root):
    st = ttk.Style(root)
    if "night" not in st.theme_names():
        st.theme_create("night", parent="clam")
    st.theme_use("night")

    st.configure(".", background=PANEL, foreground=TEXT, font=F["body"], troughcolor=PANEL,
                 bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL, focuscolor=PANEL,
                 selectbackground=mix(PANEL, RED, 0.35), selectforeground=TEXT, fieldbackground=FIELD,
                 insertcolor=TEXT, arrowcolor=MUTED, relief="flat")
    st.map(".", foreground=[("disabled", DIM)])
    st.configure("TFrame", background=PANEL)
    st.configure("Page.TFrame", background=PAGE)
    st.configure("TLabel", background=PANEL, foreground=TEXT, font=F["body"])
    st.configure("Muted.TLabel", foreground=MUTED, font=F["small"])

    # buttons: rounded hairline "ghost" buttons, red wash on hover
    r = px(7)
    n = 2 * r + 3

    def btn_images(fill, border):
        return _keep(widen(rounded(n, n, r, fill, border), r + 1))
    b_norm, b_hover = btn_images(SURFACE, LINE), btn_images(HOVER, RED_LINE)
    b_press, b_dis = btn_images(PRESS, RED), btn_images(PANEL, LINE)
    b_focus = btn_images(SURFACE, mix(LINE, FOCUS, 0.55))
    st.element_create("Night.button", "image", b_norm, ("disabled", b_dis), ("pressed", b_press),
                      ("active", b_hover), ("focus", b_focus), border=r + 1, sticky="nsew", padding=0,
                      width=n, height=n)      # the images are widened (see widen()); keep the small size
    st.layout("TButton", [("Night.button", {"sticky": "nsew", "children": [
        ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
    st.configure("TButton", padding=(px(12), px(6)), font=F["medium"], foreground=TEXT, anchor="center")
    st.map("TButton", foreground=[("disabled", DIM)])
    st.configure("Icon.TButton", padding=(px(6), px(2)), font=F["icon"], foreground=MUTED)
    st.map("Icon.TButton", foreground=[("disabled", DIM), ("active", DANGER)])

    # fields: entries and comboboxes
    fr = px(7)
    fn = 2 * fr + 3
    f_norm = _keep(widen(rounded(fn, fn, fr, FIELD, LINE), fr + 1, 480))
    f_hover = _keep(widen(rounded(fn, fn, fr, FIELD, SOFT_LINE), fr + 1, 480))
    f_focus = _keep(widen(rounded(fn, fn, fr, FIELD, FOCUS), fr + 1, 480))
    f_dis = _keep(widen(rounded(fn, fn, fr, PANEL, LINE), fr + 1, 480))
    st.element_create("Night.field", "image", f_norm, ("disabled", f_dis), ("focus", f_focus),
                      ("hover", f_hover), border=fr + 1, sticky="nsew", padding=0, width=fn, height=fn)
    st.layout("TEntry", [("Night.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    st.configure("TEntry", padding=(px(10), px(6)), foreground=TEXT, selectbackground=mix(FIELD, RED, 0.45))
    st.map("TEntry", foreground=[("disabled", DIM), ("readonly", MUTED)])
    # readonly entries (the game path) take no focus ring
    st.element_create("Night.rofield", "image", f_norm, ("disabled", f_dis), border=fr + 1, sticky="nsew", padding=0,
                      width=fn, height=fn)
    st.layout("Path.TEntry", [("Night.rofield", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
    st.configure("Path.TEntry", padding=(px(10), px(6)), foreground=MUTED, selectbackground=mix(FIELD, RED, 0.45))

    aw, ah = px(28), px(14)
    chev = [(aw * .30, ah * .36), (aw * .43, ah * .64), (aw * .56, ah * .36)]

    def arrow(colour):
        return _keep(paint(aw, ah, [(sd_polyline(chev, max(1.4, px(1.6))), colour, 1)]))
    st.element_create("Night.downarrow", "image", arrow(MUTED), ("disabled", arrow(DIM)), ("pressed", arrow(RED)),
                      ("active", arrow(TEXT)), sticky="", padding=0)
    st.layout("TCombobox", [("Night.field", {"sticky": "nsew", "children": [
        ("Night.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"expand": "1", "sticky": "nsew", "children": [
            ("Combobox.textarea", {"sticky": "nsew"})]})]})])
    st.configure("TCombobox", padding=(px(10), px(6), px(2), px(6)), foreground=TEXT, background=FIELD,
                 bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, arrowcolor=MUTED, arrowsize=px(11),
                 selectbackground=FIELD, selectforeground=TEXT, fieldbackground=FIELD)
    st.map("TCombobox", foreground=[("disabled", DIM), ("readonly", TEXT)],
           background=[("disabled", PANEL), ("active", FIELD), ("pressed", FIELD)],
           bordercolor=[("disabled", PANEL)], lightcolor=[("disabled", PANEL)], darkcolor=[("disabled", PANEL)],
           arrowcolor=[("disabled", DIM), ("active", TEXT), ("pressed", RED)],
           selectbackground=[("readonly", FIELD)], selectforeground=[("disabled", DIM), ("readonly", TEXT)],
           fieldbackground=[("disabled", PANEL)])
    root.option_add("*TCombobox*Listbox.background", POP)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", mix(POP, RED, 0.30))
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", F["body"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)
    root.option_add("*TCombobox*Listbox.activeStyle", "none")
    st.configure("ComboboxPopdownFrame", background=POP, bordercolor=LINE, lightcolor=POP, darkcolor=POP,
                 relief="solid", borderwidth=1)

    # check boxes and radio buttons
    cs = px(16)
    cr = px(4)
    cw = cs + px(9)          # the box plus the gap before its text
    outer, inner = sd_box(0, 0, cs, cs, cr), sd_box(1, 1, cs - 1, cs - 1, cr - 1)

    def box(border, fill):
        return _keep(paint(cw, cs, [(outer, border, 1), (inner, fill, 1)]))
    box_off, box_hover, box_dis = box(SOFT_LINE, FIELD), box(RED_LINE, FIELD), box(LINE, PANEL)
    tick = sd_polyline([(cs * .26, cs * .52), (cs * .43, cs * .69), (cs * .75, cs * .33)], max(1.6, cs * .12))
    box_on = _keep(paint(cw, cs, [(outer, RED, 1), (tick, "#ffffff", 1)]))
    box_on_dis = _keep(paint(cw, cs, [(outer, mix(PANEL, RED, 0.35), 1), (tick, DIM, 1)]))
    st.element_create("Night.check", "image", box_off, ("disabled", "selected", box_on_dis), ("disabled", box_dis),
                      ("selected", box_on), ("active", box_hover), sticky="")
    st.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        ("Night.check", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})])
    st.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=(0, px(3)), font=F["body"])
    st.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", DIM)])

    c = cs / 2
    ring = sd_circle(c, c, c)
    inner = sd_circle(c, c, c - 1)
    rad_off = _keep(paint(cs, cs, [(ring, SOFT_LINE, 1), (inner, FIELD, 1)]))
    rad_hover = _keep(paint(cs, cs, [(ring, RED_LINE, 1), (inner, FIELD, 1)]))
    rad_dis = _keep(paint(cs, cs, [(ring, LINE, 1), (inner, PANEL, 1)]))
    rad_on = _keep(paint(cs, cs, [(ring, RED, 1), (sd_circle(c, c, cs * .19), "#ffffff", 1)]))
    rad_on_dis = _keep(paint(cs, cs, [(ring, mix(PANEL, RED, 0.35), 1), (sd_circle(c, c, cs * .19), DIM, 1)]))
    st.element_create("Night.radio", "image", rad_off, ("disabled", "selected", rad_on_dis), ("disabled", rad_dis),
                      ("selected", rad_on), ("active", rad_hover), sticky="")
    st.layout("TRadiobutton", [("Radiobutton.padding", {"sticky": "nsew", "children": [
        ("Night.radio", {"side": "left", "sticky": ""})]})])
    st.configure("TRadiobutton", background=PANEL, padding=px(4))
    st.map("TRadiobutton", background=[("active", PANEL)])

    # scale: thin track, round knob
    th = px(4)
    tw = 2 * th + 3
    trough = _keep(rounded(tw, th, th / 2, LINE))
    st.element_create("Night.strough", "image", trough, border=(th, 0), sticky="ew", padding=0)
    ks = px(16)
    knob = _keep(paint(ks, ks, [(sd_circle(ks / 2, ks / 2, ks / 2), TEXT, 1)]))
    knob_hot = _keep(paint(ks, ks, [(sd_circle(ks / 2, ks / 2, ks / 2), RED, 1),
                                    (sd_circle(ks / 2, ks / 2, ks * .22), "#ffffff", 1)]))
    knob_dis = _keep(paint(ks, ks, [(sd_circle(ks / 2, ks / 2, ks / 2), DIM, 1)]))
    st.element_create("Night.slider", "image", knob, ("disabled", knob_dis), ("pressed", knob_hot),
                      ("active", knob_hot), sticky="")
    st.layout("Horizontal.TScale", [("Horizontal.Scale.padding", {"sticky": "nsew", "children": [
        ("Night.strough", {"sticky": "ew", "children": [("Night.slider", {"side": "left", "sticky": ""})]})]})])
    st.configure("Horizontal.TScale", background=PANEL, padding=(0, px(4)))

    # progress bar
    ph = px(8)
    pw = 2 * ph + 3
    p_trough = _keep(rounded(pw, ph, ph / 2, SURFACE, LINE))
    p_bar = _keep(rounded(pw, ph, ph / 2, RED))
    st.element_create("Night.ptrough", "image", p_trough, border=(ph // 2 + 1, 0), sticky="nsew", padding=0)
    st.element_create("Night.pbar", "image", p_bar, border=(ph // 2 + 1, 0), sticky="nsew", padding=0)
    st.layout("Horizontal.TProgressbar", [("Night.ptrough", {"sticky": "nsew", "children": [
        ("Night.pbar", {"side": "left", "sticky": "ns"})]})])
    st.configure("Horizontal.TProgressbar", thickness=ph, background=PANEL)

    # scrollbar: slim, no arrows
    sw = px(8)
    sh = 2 * sw + 3
    thumb = _keep(rounded(sw, sh, sw / 2, LINE))
    thumb_hot = _keep(rounded(sw, sh, sw / 2, SOFT_LINE))
    st.element_create("Night.vthumb", "image", thumb, ("pressed", thumb_hot), ("active", thumb_hot),
                      border=(0, sw // 2 + 1), sticky="nsew", padding=0)
    st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Night.vthumb", {"expand": "1", "sticky": "nsew"})]})])
    st.configure("Vertical.TScrollbar", troughcolor=PANEL, background=PANEL, bordercolor=PANEL,
                 lightcolor=PANEL, darkcolor=PANEL, gripcount=0, arrowsize=sw)
    st.configure("Page.Vertical.TScrollbar", troughcolor=PAGE, background=PAGE, bordercolor=PAGE,
                 lightcolor=PAGE, darkcolor=PAGE)
    hthumb = _keep(rounded(sh, sw, sw / 2, LINE))
    hthumb_hot = _keep(rounded(sh, sw, sw / 2, SOFT_LINE))
    st.element_create("Night.hthumb", "image", hthumb, ("pressed", hthumb_hot), ("active", hthumb_hot),
                      border=(sw // 2 + 1, 0), sticky="nsew", padding=0)
    st.layout("Page.Horizontal.TScrollbar", [("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [
        ("Night.hthumb", {"expand": "1", "sticky": "nsew"})]})])
    st.configure("Page.Horizontal.TScrollbar", troughcolor=PAGE, background=PAGE, bordercolor=PAGE,
                 lightcolor=PAGE, darkcolor=PAGE, gripcount=0, arrowsize=sw)
    return st


# ----------------------------------------------------------------------------------------------
# Canvas widgets
# ----------------------------------------------------------------------------------------------
class Tracked(tk.Canvas):
    """Uppercase text with letter-spacing (tkinter fonts have no tracking): one canvas item per glyph."""

    def __init__(self, parent, text, font, fg=TEXT, track=0.08, bg=PANEL, upper=True, **kw):
        tk.Canvas.__init__(self, parent, bg=bg, highlightthickness=0, bd=0, **kw)
        self.font, self.fg, self.track, self.upper = font, fg, track, upper
        self.set(text)

    def set(self, text=None, fg=None):
        if text is not None:
            self.text = text.upper() if self.upper else text
        if fg is not None:
            self.fg = fg
        self.delete("all")
        gap = self.track * abs(self.font.cget("size"))
        x = 0.0
        for ch in self.text:
            self.create_text(x, 0, text=ch, font=self.font, fill=self.fg, anchor="nw")
            x += self.font.measure(ch) + gap
        self.configure(width=max(1, int(round(x - gap if self.text else 0))), height=self.font.metrics("linespace"))


def tracked_width(font, text, track):
    size = abs(font.cget("size"))
    return sum(font.measure(c) for c in text) + track * size * max(len(text) - 1, 0)


class Chip(tk.Canvas):
    """Small uppercase badge with a rounded hairline outline (red, neutral or green)."""
    TONES = {"red": (RED, mix(PANEL, RED, 0.08), mix(PANEL, RED, 0.55)),
             "neutral": (MUTED, SURFACE, LINE),
             "green": (OK, mix(PANEL, OK, 0.08), mix(PANEL, OK, 0.45)),
             "gold": (GOLD, mix(PANEL, GOLD, 0.08), mix(PANEL, GOLD, 0.45))}

    def __init__(self, parent, text, tone="red", bg=PANEL):
        tk.Canvas.__init__(self, parent, bg=bg, highlightthickness=0, bd=0)
        self.bg = bg
        self._key = None
        self.set(text, tone)

    def set(self, text, tone=None):
        tone = tone or self.tone
        if (text, tone) == self._key:
            return
        self._key, self.text, self.tone = (text, tone), text.upper(), tone
        fg, fill, line = self.TONES[tone]
        f = F["chip"]
        w = int(round(tracked_width(f, self.text, 0.10))) + px(12)
        h = f.metrics("linespace") + px(4)
        self.img = rounded(w, h, px(4), fill, line, bg=self.bg)
        self.delete("all")
        self.configure(width=w, height=h)
        self.create_image(0, 0, image=self.img, anchor="nw")
        x = px(6)
        gap = 0.10 * abs(f.cget("size"))
        for ch in self.text:
            self.create_text(x, h / 2, text=ch, font=f, fill=fg, anchor="w")
            x += f.measure(ch) + gap


class Swatch(tk.Canvas):
    def __init__(self, parent, colour, bg=PANEL):
        self.size = px(26)
        tk.Canvas.__init__(self, parent, width=self.size, height=self.size, bg=bg, highlightthickness=0, bd=0)
        self.bg = bg
        self._colour = None
        self.set(colour)

    _cache = {}

    def set(self, colour):
        if colour == self._colour:
            return
        self._colour = colour
        s = self.size
        key = (s, colour, self.bg)
        if key not in Swatch._cache:
            Swatch._cache[key] = paint(s, s, [(sd_box(0, 0, s, s, px(6)), SOFT_LINE, 1),
                                              (sd_box(px(3), px(3), s - px(3), s - px(3), px(4)), colour, 1)], self.bg)
        self.img = Swatch._cache[key]
        self.delete("all")
        self.create_image(0, 0, image=self.img, anchor="nw")


class AngledButton(tk.Canvas):
    """The red primary action: a parallelogram (two opposite corners cut at 45 degrees).
    Speaks enough of ttk's state() for clone_gui's enable/disable loop."""

    def __init__(self, parent, text, command, bg=PANEL, height=44):
        f = F["button"]
        self.text = text.upper()
        self.cut = px(9)
        h = px(height)
        w = int(round(tracked_width(f, self.text, 0.08))) + px(44) + self.cut
        tk.Canvas.__init__(self, parent, width=w, height=h, bg=bg, highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.command = command
        self._states = set()
        self._hover = self._down = False
        shape = sd_convex([(self.cut, 0), (w, 0), (w - self.cut, h), (0, h)])
        self.imgs = {k: paint(w, h, [(shape, c, 1)], bg) for k, c in
                     (("normal", RED), ("hover", FOCUS), ("press", mix(RED, "#000000", 0.18)),
                      ("disabled", mix(PANEL, RED, 0.22)))}
        self.bg_item = self.create_image(0, 0, anchor="nw")
        x = (w - tracked_width(f, self.text, 0.08)) / 2
        gap = 0.08 * abs(f.cget("size"))
        self.glyphs = []
        for ch in self.text:
            self.glyphs.append(self.create_text(x, h / 2 + px(1), text=ch, font=f, anchor="w"))
            x += f.measure(ch) + gap
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self._draw()

    def _draw(self):
        dis = "disabled" in self._states
        key = "disabled" if dis else "press" if self._down else "hover" if self._hover else "normal"
        self.itemconfigure(self.bg_item, image=self.imgs[key])
        for g in self.glyphs:
            self.itemconfigure(g, fill=mix(MUTED, PANEL, 0.25) if dis else "#ffffff")
        self.configure(cursor="" if dis else "hand2")

    def _set_hover(self, on):
        self._hover = on
        if not on:
            self._down = False
        self._draw()

    def _press(self, e):
        if "disabled" not in self._states:
            self._down = True
            self.focus_set()
            self._draw()

    def _release(self, e):
        fire = self._down and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._down = False
        self._draw()
        if fire:
            self.invoke()

    def invoke(self):
        if "disabled" not in self._states and self.command:
            self.command()

    def state(self, spec=None):
        old = tuple(sorted(self._states))
        if spec is not None:
            for s in spec:
                if s.startswith("!"):
                    self._states.discard(s[1:])
                else:
                    self._states.add(s)
            self._draw()
        return old

    def instate(self, spec):
        return all((s[1:] not in self._states) if s.startswith("!") else (s in self._states) for s in spec)


# ----------------------------------------------------------------------------------------------
# The page: ambient red wash, header, rounded hairline panels
# ----------------------------------------------------------------------------------------------
_WASH = None   # (page, red - page, gold - page) per channel, filled on first use


def _wash_rgb(u, v):
    """Page background colour at relative position (u = x/w - 0.5, v = y/h)."""
    global _WASH
    if _WASH is None:
        _WASH = [(p, r - p, g - p) for p, r, g in zip(rgb(PAGE), rgb(RED), rgb(GOLD))]
    dr = math.hypot(u / 1.2, (v + 0.1) / 0.8)
    dg = math.hypot(u / 1.2, (v - 1.1) / 0.6)
    ar = 0.16 * max(0.0, 1 - dr / 0.68)
    ag = 0.05 * max(0.0, 1 - dg / 0.55)
    return tuple(int(p + dr_ * ar + dg_ * ag + 0.5) for p, dr_, dg_ in _WASH)


def wash_colour(x, y, w, h):
    """The wash colour under page pixel (x, y), rounded so nearby spots share cached images."""
    c = _wash_rgb(x / max(w, 1) - 0.5, y / max(h, 1))
    return hexc(tuple(v & ~1 for v in c))


def wash_png(w, h, z):
    """The page background at 1/z size: a red bleed from the top and a faint gold one from the bottom
    (CSS: radial-gradient(120% 80% at 50% -10%, red/.16, transparent 68%) +
          radial-gradient(120% 60% at 50% 110%, gold/.05, transparent 55%))."""
    ww, wh = max(1, -(-w // z)), max(1, -(-h // z))
    page, red, gold = rgb(PAGE), rgb(RED), rgb(GOLD)
    rows = []
    for y in range(wh):
        v = (y + 0.5) * z / h
        row = bytearray(b"\0")
        for x in range(ww):
            row += bytes(_wash_rgb((x + 0.5) * z / w - 0.5, v))
        rows.append(bytes(row))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", ww, wh, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))
    return png


class Card(tk.Frame):
    """A panel: header row (chip, tracked title, muted note, optional right-hand widgets), a hairline,
    and .body.  The rounded outline is drawn by the Page behind the frame."""

    def __init__(self, page, title, chip=None, note=None, pad=16):
        tk.Frame.__init__(self, page, bg=PANEL)
        self.page = page
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.head = tk.Frame(self, bg=PANEL)
        self.head.grid(row=0, column=0, sticky="ew", padx=px(pad - 4), pady=(px(8), px(8)))
        c = 0
        if chip:
            self.chip = Chip(self.head, chip, "red")
            self.chip.grid(row=0, column=c, padx=(0, px(10)))
            c += 1
        self.title = Tracked(self.head, title, F["head"], TEXT, 0.08)
        self.title.grid(row=0, column=c, sticky="w")
        c += 1
        if note:
            tk.Label(self.head, text=note, bg=PANEL, fg=MUTED, font=F["small"]).grid(row=0, column=c, sticky="w", padx=(px(10), 0))
            c += 1
        self.head.columnconfigure(c, weight=1)
        self.right = tk.Frame(self.head, bg=PANEL)
        self.right.grid(row=0, column=c + 1, sticky="e")
        tk.Frame(self, bg=LINE, height=1).grid(row=1, column=0, sticky="ew")
        self.body = tk.Frame(self, bg=PANEL)
        self.body.grid(row=2, column=0, sticky="nsew", padx=px(pad - 4), pady=(px(11), px(pad - 4)))
        self.bind("<Configure>", lambda e: page.schedule_card(self))


def section(parent, text, red=False, row=0, column=0, columnspan=1, pady=(0, 6)):
    """A small uppercase kicker/field label (muted, or red for emphasis)."""
    lbl = Tracked(parent, text, F["kicker"] if red else F["label"], RED if red else MUTED, 0.12 if red else 0.06)
    lbl.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=(px(pady[0]), px(pady[1])))
    return lbl


class Page(tk.Canvas):
    """Fills the window: wash background, header, and the cards (gridded straight into the canvas,
    so the wash shows in the gutters)."""

    def __init__(self, root, title, subtitle, blurb, header_h=58):
        tk.Canvas.__init__(self, root, bg=PAGE, highlightthickness=0, bd=0)
        self.root = root
        self.header_h = px(header_h)
        self.cards = []
        self._hot = None
        self._wash_job = None
        self._wash_size = None
        self._cards_job = None
        self._dirty_cards = set()
        self.wash_item = self.create_image(0, 0, anchor="nw", tags=("wash",))
        self.r = px(12)
        self.inset = px(5)
        self._corner_cache = {}
        # header
        x = px(24)
        cy = self.header_h // 2
        self.mark = mark_image(px(30), wash_colour(x, cy, px(1320), px(780)))
        self.create_image(x, cy, image=self.mark, anchor="w")
        x += px(30) + px(12)
        tf, kf = F["title"], F["kicker"]
        th = tf.metrics("ascent") + px(3) + kf.metrics("linespace")
        ty = cy - th // 2
        self._tracked_text(x, ty, title, tf, TEXT, 0.08)
        self._tracked_text(x, ty + tf.metrics("ascent") + px(3), subtitle, kf, MUTED, 0.12)
        x += max(tracked_width(tf, title.upper(), 0.08), tracked_width(kf, subtitle.upper(), 0.12)) + px(22)
        self.create_line(x, cy - px(14), x, cy + px(14), fill=LINE)
        self.blurb = self.create_text(x + px(22), cy, text=blurb, font=F["body"], fill=MUTED, anchor="w")
        self._blurb_right = x + px(22) + F["body"].measure(blurb)
        self.header_line = self.create_line(0, self.header_h - 1, 0, self.header_h - 1, fill=LINE)
        # status pill (top right)
        self.pill_bg = self.create_image(0, cy, anchor="e")
        self.pill_dot = self.create_oval(0, 0, 0, 0, outline="")
        self.pill_text = self.create_text(0, cy, text="", font=F["medium"], fill=TEXT, anchor="e")
        self._pill = ("", MUTED)
        self.set_pill("No game yet", MUTED)
        self.bind("<Configure>", self._resized)
        self.bind_all("<Motion>", self._motion, add="+")
        self.winfo_toplevel().bind("<Leave>", lambda e: self._set_hot(None), add="+")

    def _tracked_text(self, x, y, text, font, fill, track):
        gap = track * abs(font.cget("size"))
        for ch in text.upper():
            self.create_text(x, y, text=ch, font=font, fill=fill, anchor="nw")
            x += font.measure(ch) + gap

    # ---- status pill
    def set_pill(self, text, colour):
        self._pill = (text, colour)
        f = F["medium"]
        h = px(30)
        w = f.measure(text) + px(46)
        W, H = max(self.winfo_width(), px(1320)), max(self.winfo_height(), px(780))
        self._pill_bg = wash_colour(W - px(24) - w / 2, self.header_h // 2, W, H)
        self._pill_img = rounded(w, h, h / 2, mix(PAGE, SURFACE, 0.85), LINE, bg=self._pill_bg)
        self.itemconfigure(self.pill_bg, image=self._pill_img)
        self.itemconfigure(self.pill_text, text=text)
        self.itemconfigure(self.pill_dot, fill=colour)
        self._pill_w = w
        self._place_pill()

    def _place_pill(self):
        W = self.winfo_width()
        cy = self.header_h // 2
        right = W - px(24)
        self.coords(self.pill_bg, right, cy)
        self.coords(self.pill_text, right - px(16), cy)
        dx = right - self._pill_w + px(15)
        d = px(8)
        self.coords(self.pill_dot, dx, cy - d / 2, dx + d, cy + d / 2)
        # a narrow window: the status pill wins over the one-line blurb
        self.itemconfigure(self.blurb, state="normal" if self._blurb_right + px(16) < right - self._pill_w else "hidden")

    # ---- wash + layout
    def _resized(self, e):
        self.coords(self.header_line, 0, self.header_h - 1, e.width, self.header_h - 1)
        W, H = max(e.width, px(1320)), max(e.height, px(780))
        if wash_colour(W - px(24) - self._pill_w / 2, self.header_h // 2, W, H) != self._pill_bg:
            self.set_pill(*self._pill)
        self._place_pill()
        if self._wash_job is not None:
            self.after_cancel(self._wash_job)
        self._wash_job = self.after(250, self._draw_wash)   # once the drag has stopped: ~0.1 s of Python

    def _draw_wash(self):
        self._wash_job = None
        w, h = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        if self._wash_size == (w, h):
            return
        self._wash_size = (w, h)
        z = px(8)
        img = tk.PhotoImage(data=base64.b64encode(wash_png(w, h, z)).decode("ascii"))
        self._wash = img.zoom(z)
        self.itemconfigure(self.wash_item, image=self._wash)
        self.tag_lower("wash")

    def add_card(self, card, **grid):
        card.grid(**grid)
        self.cards.append(card)
        return card

    def _corner(self, hot, name, bg):
        """One rounded panel corner, opaque on the wash colour `bg` under it."""
        key = (hot, name, bg)
        if key not in self._corner_cache:
            r, g = self.r, px(4)
            n = r + g
            border = RED_LINE if hot else LINE
            big = 4 * r                                           # a big box; each image keeps one corner of it
            x0, y0 = {"nw": (g, g), "ne": (n - g - big, g), "sw": (g, n - g - big), "se": (n - g - big, n - g - big)}[name]
            box = sd_box(x0, y0, x0 + big, y0 + big, r)
            inner = sd_box(x0 + 1, y0 + 1, x0 + big - 1, y0 + big - 1, r - 1)
            layers = []
            if hot:
                layers.append((lambda x, y, box=box: box(x, y) - g, RED, 0.06))
                layers.append((lambda x, y, box=box: box(x, y) - g / 2, RED, 0.08))
            layers += [(box, border, 1), (inner, PANEL, 1)]
            self._corner_cache[key] = paint(n, n, layers, bg)
        return self._corner_cache[key]

    def schedule_card(self, card):
        """Redraw a card's panel at most every 50 ms: a window drag sends a resize per pixel, and each
        redraw can build new corner images (pure Python) for the wash colour under them."""
        self._dirty_cards.add(card)
        if self._cards_job is None:
            self._cards_job = self.after(50, self._draw_dirty_cards)

    def _draw_dirty_cards(self):
        self._cards_job = None
        cards, self._dirty_cards = self._dirty_cards, set()
        for card in cards:
            if card.winfo_exists():
                self.draw_card(card)

    def draw_card(self, card):
        tag = "card%d" % id(card)
        self.delete(tag)
        if not card.winfo_ismapped():
            return
        hot = card is self._hot
        m, r, g = self.inset, self.r, px(4)
        x0, y0 = card.winfo_x() - m, card.winfo_y() - m
        x1, y1 = card.winfo_x() + card.winfo_width() + m, card.winfo_y() + card.winfo_height() + m
        border = RED_LINE if hot else LINE
        t = (tag, "card")
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=PANEL, outline="", tags=t)
        self.create_rectangle(x0, y0 + r, x1, y1 - r, fill=PANEL, outline="", tags=t)
        if hot:
            for i, a in ((1, 0.14), (2, 0.08), (3, 0.04)):
                c = mix(PAGE, RED, a)
                self.create_line(x0 + r, y0 - i, x1 - r, y0 - i, fill=c, tags=t)
                self.create_line(x0 + r, y1 - 1 + i, x1 - r, y1 - 1 + i, fill=c, tags=t)
                self.create_line(x0 - i, y0 + r, x0 - i, y1 - r, fill=c, tags=t)
                self.create_line(x1 - 1 + i, y0 + r, x1 - 1 + i, y1 - r, fill=c, tags=t)
        self.create_line(x0 + r, y0, x1 - r, y0, fill=border, tags=t)
        self.create_line(x0 + r, y1 - 1, x1 - r, y1 - 1, fill=border, tags=t)
        self.create_line(x0, y0 + r, x0, y1 - r, fill=border, tags=t)
        self.create_line(x1 - 1, y0 + r, x1 - 1, y1 - r, fill=border, tags=t)
        W, H = max(self.winfo_width(), 2), max(self.winfo_height(), 2)
        for name, x, y, anchor in (("nw", x0 - g, y0 - g, "nw"), ("ne", x1 + g, y0 - g, "ne"),
                                   ("sw", x0 - g, y1 + g, "sw"), ("se", x1 + g, y1 + g, "se")):
            self.create_image(x, y, image=self._corner(hot, name, wash_colour(x, y, W, H)), anchor=anchor, tags=t)
        self.tag_lower("card")
        self.tag_lower("wash")

    def redraw(self):
        for c in self.cards:
            self.draw_card(c)

    def _motion(self, e):
        w = e.widget
        hot = None
        while isinstance(w, tk.Misc):
            if isinstance(w, Card):
                hot = w
                break
            w = w.master
        if hot is not None and hot.page is not self:
            hot = None
        self._set_hot(hot)

    def _set_hot(self, card):
        if card is self._hot:
            return
        old, self._hot = self._hot, card
        for c in (old, card):
            if c is not None:
                self.draw_card(c)


class Scroller(tk.Frame):
    """Hosts the Page and scrolls it when the window is smaller than its content (small screens, high
    DPI): vertically, and sideways only as a last resort (the App stacks its columns first).  The slim
    scrollbars only show when they are needed."""

    def __init__(self, root):
        tk.Frame.__init__(self, root, bg=PAGE)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.view = tk.Canvas(self, bg=PAGE, highlightthickness=0, bd=0, yscrollincrement=px(24), xscrollincrement=px(24))
        self.view.grid(row=0, column=0, sticky="nsew")
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.view.yview, style="Page.Vertical.TScrollbar")
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.view.xview, style="Page.Horizontal.TScrollbar")
        self.view.configure(yscrollcommand=self.bar.set, xscrollcommand=self.hbar.set)
        self.page = self.item = None
        self._req = None
        self._fit_job = None
        self.view.bind("<Configure>", lambda e: self._schedule_fit())
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Button-4>", self._wheel, add="+")
        self.bind_all("<Button-5>", self._wheel, add="+")

    def host(self, page):
        self.page = page
        self.item = self.view.create_window(0, 0, window=page, anchor="nw")
        self._watch()

    def _watch(self):                      # the content grows/shrinks (rows, Advanced): refit
        req = (self.page.winfo_reqwidth(), self.page.winfo_reqheight())
        if req != self._req:
            self._req = req
            self.fit()
        self.after(150, self._watch)

    def _schedule_fit(self):
        """Resize the page at most every 40 ms: each fit re-lays out every widget on it (~40 ms in Tk on
        Windows), and a window drag sends a resize per mouse move."""
        if self._fit_job is None:
            self._fit_job = self.after(40, self._fit_now)

    def _fit_now(self):
        self._fit_job = None
        self.fit()

    def fit(self):
        if self.page is None:
            return
        vw, vh = self.view.winfo_width(), self.view.winfo_height()
        rw, rh = self.page.winfo_reqwidth(), self.page.winfo_reqheight()
        w, h = max(vw, rw), max(vh, rh)
        self.view.itemconfigure(self.item, width=w, height=h)
        self.view.configure(scrollregion=(0, 0, w, h))
        if rh > vh + 1 and vh > 1:
            if not self.bar.winfo_ismapped():
                self.bar.grid(row=0, column=1, sticky="ns")
        elif self.bar.winfo_ismapped():
            self.bar.grid_remove()
            self.view.yview_moveto(0)
        if rw > vw + 1 and vw > 1:
            if not self.hbar.winfo_ismapped():
                self.hbar.grid(row=1, column=0, sticky="ew")
        elif self.hbar.winfo_ismapped():
            self.hbar.grid_remove()
            self.view.xview_moveto(0)

    def _wheel(self, e):
        if not self.bar.winfo_ismapped() or not isinstance(e.widget, tk.Misc):
            return
        if isinstance(e.widget, (tk.Text, tk.Listbox)) or e.widget.winfo_class() == "TCombobox":
            return                          # those scroll (or change) themselves
        if e.widget.winfo_toplevel() is not self.winfo_toplevel():
            return
        up = e.num == 4 or getattr(e, "delta", 0) > 0
        if e.state & 1 and self.hbar.winfo_ismapped():      # Shift+wheel: sideways
            self.view.xview_scroll(-3 if up else 3, "units")
        else:
            self.view.yview_scroll(-3 if up else 3, "units")


# ----------------------------------------------------------------------------------------------
# Windows niceties
# ----------------------------------------------------------------------------------------------
def dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def dark_title_bar(win):
    """Best effort: a dark title bar on Windows 10 (1809+) / 11, caption tinted to the page colour.
    Returns True if Windows accepted the dark mode attribute."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        on = ctypes.c_int(1)
        ok = dwm(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on)) == 0      # DWMWA_USE_IMMERSIVE_DARK_MODE
        if not ok:
            ok = dwm(hwnd, 19, ctypes.byref(on), ctypes.sizeof(on)) == 0  # its pre-20H1 number
        r, g, b = rgb(PAGE)
        colour = ctypes.c_int(r | (g << 8) | (b << 16))
        dwm(hwnd, 35, ctypes.byref(colour), ctypes.sizeof(colour))       # DWMWA_CAPTION_COLOR (Windows 11)
        # repaint the frame now (SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        return ok
    except Exception:
        return False


# ----------------------------------------------------------------------------------------------
# Themed dialogs (same calls as tkinter.messagebox)
# ----------------------------------------------------------------------------------------------
class Dialogs:
    def __init__(self, root):
        self.root = root

    def showinfo(self, title, message):
        self._run(title, message, "done", "green", [("OK", True, True)])

    def showerror(self, title, message):
        self._run(title, message, "error", "red", [("OK", True, True)])

    def askyesno(self, title, message):
        return self._run(title, message, "confirm", "neutral", [("No", False, False), ("Yes", True, True)])

    def _run(self, title, message, chip, tone, buttons):
        root = self.root
        top = tk.Toplevel(root, bg=LINE)
        top.withdraw()
        top.title(title)
        top.resizable(False, False)
        top.transient(root)
        inner = tk.Frame(top, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(inner, bg=PANEL)
        head.pack(fill="x", padx=px(18), pady=(px(14), px(12)))
        Chip(head, chip, tone).pack(side="left", padx=(0, px(10)))
        Tracked(head, title, F["head"], TEXT, 0.08).pack(side="left")
        tk.Frame(inner, bg=LINE, height=1).pack(fill="x")
        tk.Label(inner, text=message, bg=PANEL, fg=TEXT, font=F["body"], justify="left", anchor="w",
                 wraplength=px(480)).pack(fill="x", padx=px(18), pady=(px(16), px(18)))
        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", padx=px(18), pady=(0, px(16)))
        result = {"v": buttons[0][1] if len(buttons) > 1 else True}

        def close(v):
            result["v"] = v
            top.grab_release()
            top.destroy()
        primary = None
        for text, value, is_primary in reversed(buttons):
            if is_primary:
                b = AngledButton(row, text, lambda v=value: close(v), height=36)
                primary = b
            else:
                b = ttk.Button(row, text=text, command=lambda v=value: close(v))
            b.pack(side="right", padx=(px(8), 0))
        cancel = buttons[0][1] if len(buttons) > 1 else True
        top.bind("<Escape>", lambda e: close(cancel))
        top.bind("<Return>", lambda e: primary.invoke())
        top.protocol("WM_DELETE_WINDOW", lambda: close(cancel))
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + max((root.winfo_height() - h) // 3, 0)
        top.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        top.deiconify()
        dark_title_bar(top)
        try:
            top.grab_set()
        except tk.TclError:
            pass
        primary.focus_set()
        root.wait_window(top)
        return result["v"]


# ----------------------------------------------------------------------------------------------
def init(root):
    """Scale, fonts, ttk theme, window colours.  Call once, right after tk.Tk()."""
    global S
    S = max(1.0, round(root.winfo_fpixels("1i") / 96.0 * 4) / 4)
    _make_fonts(root)
    _build_style(root)
    root.configure(bg=PAGE)
    root.option_add("*Toplevel.background", PAGE)
    try:
        icons = [_keep(mark_image(n)) for n in (16, 32, 48)]
        root.iconphoto(True, *icons)
    except tk.TclError:
        pass
    return S
