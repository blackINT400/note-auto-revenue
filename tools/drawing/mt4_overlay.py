#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MT4オーバーレイ描画ツール
==========================

MT4（や任意のアプリ）の画面の「上」に重ねて、矢印・波形・図形を描き込む
常時最前面の透明オーバーレイです。費用ゼロ・追加インストール不要
（Python標準の tkinter だけで動作）。

■ 仕組み
  - 画面全体に透明な最前面ウィンドウを重ねる
  - 「描画モード」⇔「操作モード」を1ボタンで切替
      描画モード : MT4の上に自由に描ける（MT4は薄く透けて見える）
      操作モード : クリックがMT4に通り抜ける。描いた線は残したままMT4を操作できる
  - 小さなツールバーで 色 / 太さ / 図形 / 元に戻す / 全消去 / 保存 / 終了

■ 使い方（Windows）
  1. Python 3 をインストール（無料 / https://www.python.org ）
  2. このファイルをダブルクリック、または `python mt4_overlay.py`
  3. MT4を開いた状態で「描画」を押して描く → トレードしたい時は「操作」を押す

■ ショートカット
  F8       描画 ⇔ 操作 の切替
  Ctrl+Z   元に戻す
  Delete   全消去
  Esc      終了

Windows以外では透明・クリック透過が効かない場合があります（MT4はWindows想定）。
"""

import sys
import platform
import tkinter as tk
from tkinter import colorchooser

# 透明色（この色で塗られた領域は透けて見える）。描画色に使わない稀な色にする。
TRANSPARENT = "#0a0b0c"
DRAW_ALPHA = 0.35      # 描画モード時の不透明度（MT4がうっすら透ける）

IS_WINDOWS = platform.system() == "Windows"

# ---- Windows: クリック透過(WS_EX_TRANSPARENT)の切替 ----
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020

    _user32 = ctypes.windll.user32
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]


def get_hwnd(win):
    """tkinterウィンドウのネイティブHWNDを取得。"""
    if not IS_WINDOWS:
        return None
    win.update_idletasks()
    hwnd = _user32.GetParent(win.winfo_id())
    return hwnd if hwnd else win.winfo_id()


def set_click_through(hwnd, enabled):
    """enabled=True でクリックを下のウィンドウ(MT4)に通す。"""
    if not IS_WINDOWS or not hwnd:
        return
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


class OverlayApp:
    def __init__(self):
        # ===== オーバーレイ本体（全画面・透明・最前面） =====
        self.root = tk.Tk()
        self.root.title("MT4 Overlay")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{sw}x{sh}+0+0")
        self.root.overrideredirect(True)          # 枠なし
        self.root.attributes("-topmost", True)

        self.canvas = tk.Canvas(
            self.root, width=sw, height=sh,
            bg=TRANSPARENT, highlightthickness=0, cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        # 描画状態
        self.tool = "pen"           # pen / line / arrow / rect / ellipse
        self.color = "#ff3b30"
        self.size = 3
        self.draw_mode = True       # True=描画 / False=操作(クリック透過)
        self.start = None
        self.preview_id = None
        self.strokes = []           # undo用: 各ストロークのcanvas item idリスト
        self.current_items = []

        self.canvas.bind("<Button-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)

        self.overlay_hwnd = get_hwnd(self.root)

        # ===== ツールバー（小さな最前面ウィンドウ） =====
        self.build_toolbar(sw)

        # ショートカット（両ウィンドウにバインド）
        for w in (self.root, self.bar):
            w.bind("<F8>", lambda e: self.toggle_mode())
            w.bind("<Control-z>", lambda e: self.undo())
            w.bind("<Delete>", lambda e: self.clear())
            w.bind("<Escape>", lambda e: self.quit())

        self.apply_mode()

    # ------------------------------------------------------------------
    def build_toolbar(self, sw):
        self.bar = tk.Toplevel(self.root)
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.configure(bg="#2a2a38")
        # 画面上部中央に配置
        self.bar.geometry(f"+{max(0, sw // 2 - 300)}+8")

        pad = {"padx": 3, "pady": 4}
        row = tk.Frame(self.bar, bg="#2a2a38")
        row.pack(padx=6, pady=4)

        # モード切替
        self.mode_btn = tk.Button(row, text="● 描画", width=7, command=self.toggle_mode,
                                   bg="#4a9eff", fg="white", relief="flat")
        self.mode_btn.pack(side="left", **pad)

        tk.Frame(row, width=1, bg="#444").pack(side="left", fill="y", padx=4)

        # ツール
        self.tool_btns = {}
        for key, label in [("pen", "✏"), ("line", "／"), ("arrow", "➜"),
                           ("rect", "▭"), ("ellipse", "◯")]:
            b = tk.Button(row, text=label, width=3, relief="flat",
                          bg="#33333f", fg="white",
                          command=lambda k=key: self.set_tool(k))
            b.pack(side="left", **pad)
            self.tool_btns[key] = b
        self.set_tool("pen")

        tk.Frame(row, width=1, bg="#444").pack(side="left", fill="y", padx=4)

        # 色スウォッチ
        for c in ["#ff3b30", "#34c759", "#ffcc00", "#4a9eff", "#000000", "#ffffff"]:
            tk.Button(row, bg=c, width=2, relief="flat",
                      command=lambda col=c: self.set_color(col)).pack(side="left", padx=2)
        tk.Button(row, text="色…", relief="flat", bg="#33333f", fg="white",
                  command=self.pick_color).pack(side="left", **pad)

        tk.Frame(row, width=1, bg="#444").pack(side="left", fill="y", padx=4)

        # 太さ
        self.size_var = tk.IntVar(value=self.size)
        tk.Scale(row, from_=1, to=30, orient="horizontal", length=90,
                 variable=self.size_var, command=self.set_size,
                 bg="#2a2a38", fg="white", troughcolor="#33333f",
                 highlightthickness=0, showvalue=True).pack(side="left")

        tk.Frame(row, width=1, bg="#444").pack(side="left", fill="y", padx=4)

        # 操作
        tk.Button(row, text="↶", width=3, relief="flat", bg="#33333f", fg="white",
                  command=self.undo).pack(side="left", **pad)
        tk.Button(row, text="🗑", width=3, relief="flat", bg="#33333f", fg="white",
                  command=self.clear).pack(side="left", **pad)
        tk.Button(row, text="💾", width=3, relief="flat", bg="#33333f", fg="white",
                  command=self.save).pack(side="left", **pad)
        tk.Button(row, text="✕", width=3, relief="flat", bg="#5a2a2a", fg="white",
                  command=self.quit).pack(side="left", **pad)

        # ツールバーをドラッグ移動可能に
        for w in (self.bar, row):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.bar.winfo_x() + e.x - self._dx
        y = self.bar.winfo_y() + e.y - self._dy
        self.bar.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    def set_tool(self, key):
        self.tool = key
        for k, b in self.tool_btns.items():
            b.configure(bg="#4a9eff" if k == key else "#33333f")

    def set_color(self, c):
        self.color = c

    def pick_color(self):
        c = colorchooser.askcolor(color=self.color)[1]
        if c:
            self.color = c

    def set_size(self, _=None):
        self.size = int(self.size_var.get())

    # ------------------------------------------------------------------
    def toggle_mode(self):
        self.draw_mode = not self.draw_mode
        self.apply_mode()

    def apply_mode(self):
        """描画モード / 操作モード の見た目とクリック透過を反映。"""
        if self.draw_mode:
            # 描画: 全面で入力を受ける。MT4は薄く透ける。
            self.root.attributes("-transparentcolor", "")
            self.root.attributes("-alpha", DRAW_ALPHA)
            self.canvas.configure(bg="#101418")
            set_click_through(self.overlay_hwnd, False)
            self.mode_btn.configure(text="● 描画", bg="#4a9eff")
        else:
            # 操作: 空白は透明+クリック透過。描いた線だけ最前面に残す。
            self.root.attributes("-alpha", 1.0)
            self.root.attributes("-transparentcolor", TRANSPARENT)
            self.canvas.configure(bg=TRANSPARENT)
            set_click_through(self.overlay_hwnd, True)
            self.mode_btn.configure(text="○ 操作", bg="#5a5a68")
        self.bar.lift()

    # ------------------------------------------------------------------
    def on_down(self, e):
        if not self.draw_mode:
            return
        self.start = (e.x, e.y)
        self.current_items = []
        self.preview_id = None
        if self.tool == "pen":
            self._last = (e.x, e.y)

    def on_move(self, e):
        if not self.draw_mode or self.start is None:
            return
        x0, y0 = self.start
        if self.tool == "pen":
            lid = self.canvas.create_line(self._last[0], self._last[1], e.x, e.y,
                                          fill=self.color, width=self.size,
                                          capstyle="round", smooth=True)
            self.current_items.append(lid)
            self._last = (e.x, e.y)
            return
        # 図形はプレビューを描き直す
        if self.preview_id is not None:
            self.canvas.delete(self.preview_id)
        self.preview_id = self._draw_shape(x0, y0, e.x, e.y)

    def on_up(self, e):
        if not self.draw_mode or self.start is None:
            return
        if self.tool != "pen":
            if self.preview_id is not None:
                self.current_items.append(self.preview_id)
                self.preview_id = None
        if self.current_items:
            self.strokes.append(self.current_items)
        self.start = None
        self.current_items = []

    def _draw_shape(self, x0, y0, x1, y1):
        if self.tool == "line":
            return self.canvas.create_line(x0, y0, x1, y1, fill=self.color,
                                           width=self.size, capstyle="round")
        if self.tool == "arrow":
            return self.canvas.create_line(x0, y0, x1, y1, fill=self.color,
                                           width=self.size, capstyle="round",
                                           arrow="last",
                                           arrowshape=(max(12, self.size * 3),
                                                       max(15, self.size * 4),
                                                       max(5, self.size * 1.5)))
        if self.tool == "rect":
            return self.canvas.create_rectangle(x0, y0, x1, y1, outline=self.color,
                                                width=self.size)
        if self.tool == "ellipse":
            return self.canvas.create_oval(x0, y0, x1, y1, outline=self.color,
                                           width=self.size)
        return None

    # ------------------------------------------------------------------
    def undo(self):
        if self.strokes:
            for item in self.strokes.pop():
                self.canvas.delete(item)

    def clear(self):
        self.canvas.delete("all")
        self.strokes = []

    def save(self):
        """PNG保存（PillowのImageGrabがあれば画面ごと保存）。"""
        try:
            from PIL import ImageGrab
        except ImportError:
            self._toast("保存には Pillow が必要: pip install pillow")
            return
        import datetime
        was_draw = self.draw_mode
        # 一瞬だけ操作モードにして自分のveilを消してから撮影
        if was_draw:
            self.draw_mode = False
            self.apply_mode()
            self.root.update()
        fname = "mt4-annotated-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".png"
        try:
            img = ImageGrab.grab()
            img.save(fname)
            self._toast(f"保存しました: {fname}")
        except Exception as ex:  # noqa
            self._toast(f"保存失敗: {ex}")
        finally:
            if was_draw:
                self.draw_mode = True
                self.apply_mode()

    def _toast(self, msg):
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg="#222")
        tk.Label(t, text=msg, bg="#222", fg="white",
                 padx=14, pady=8).pack()
        t.geometry(f"+{self.root.winfo_screenwidth()//2-150}+60")
        t.after(2200, t.destroy)

    def quit(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if not IS_WINDOWS:
        print("注意: このツールはWindows(MT4)向けです。"
              "透明・クリック透過が効かない場合があります。", file=sys.stderr)
    OverlayApp().run()


if __name__ == "__main__":
    main()
