#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MT4オーバーレイ描画ツール（macOS版）
=====================================

MT4（や任意のアプリ）の画面の「上」に重ねて、矢印・波形・図形を描き込む
常時最前面の透明オーバーレイです。macOS の Cocoa(pyobjc) を使います。

■ 準備（初回だけ / 費用ゼロ）
    pip install pyobjc-framework-Cocoa

■ 起動
    python mt4_overlay_mac.py

■ 仕組み
  - 画面全体に透明な最前面ウィンドウを重ねる
  - 上部の小さなツールバーで「描画中 ⇔ 操作中」を切替
      描画中 : MT4の上に自由に描ける
      操作中 : クリックがMT4に通り抜ける（描いた線は残る）
  - ツール: ペン / 直線 / 矢印 / 四角 / 円、色・太さ変更、元に戻す、全消去、保存

■ ショートカット（描画中に有効）
    F8      描画 ⇔ 操作 の切替（操作中は上部ツールバーのボタンで戻す）
    Cmd+Z   元に戻す
    P/L/A/R/C  ペン/直線/矢印/四角/円

■ 保存
    ⌘ボタン(💾) で画面全体を ~/Desktop に PNG保存（macOS標準の screencapture を使用）

※ グローバルなショートカット監視は使わない設計なので、アクセシビリティ許可は不要です。
  操作中から描画中に戻すときは、上部ツールバーの「操作中」ボタンを押してください。
"""

import sys
import math
import os
import platform
import datetime
import subprocess

if platform.system() != "Darwin":
    sys.exit("このツールは macOS 専用です。Windowsでは mt4_overlay.py を使ってください。")

try:
    import objc
    from Cocoa import (
        NSApplication, NSApp, NSObject, NSWindow, NSPanel, NSView, NSButton,
        NSSlider, NSColor, NSBezierPath, NSScreen, NSEvent, NSMakeRect,
        NSApplicationActivationPolicyAccessory,
        NSWindowStyleMaskBorderless, NSWindowStyleMaskTitled,
        NSWindowStyleMaskUtilityWindow, NSWindowStyleMaskHUDWindow,
        NSBackingStoreBuffered, NSStatusWindowLevel,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorStationary,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSBezelStyleRounded, NSBezelStyleShadowlessSquare,
        NSRoundLineCapStyle, NSRoundLineJoinStyle,
        NSEventMaskKeyDown, NSEventModifierFlagCommand,
    )
except ImportError:
    sys.exit(
        "pyobjc が見つかりません。ターミナルで次を実行してください:\n"
        "    pip install pyobjc-framework-Cocoa\n"
        "その後もう一度 python mt4_overlay_mac.py を実行してください。"
    )

# 描画色のプリセット（赤=売り, 緑=買い, 黄, 青, 黒, 白）
PRESET_COLORS = [
    (1.00, 0.23, 0.19),  # 赤
    (0.20, 0.78, 0.35),  # 緑
    (1.00, 0.80, 0.00),  # 黄
    (0.29, 0.62, 1.00),  # 青
    (0.00, 0.00, 0.00),  # 黒
    (1.00, 1.00, 1.00),  # 白
]
TOOLS = ["pen", "line", "arrow", "rect", "ellipse"]
TOOL_LABELS = {"pen": "✏ ペン", "line": "／ 直線", "arrow": "➜ 矢印",
               "rect": "▭ 四角", "ellipse": "◯ 円"}


def nscolor(rgb):
    r, g, b = rgb
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


# ---------------------------------------------------------------------------
class OverlayView(NSView):
    """描画キャンバス。透明背景で、その上にストロークを描く。"""

    def initWithFrame_(self, frame):
        self = objc.super(OverlayView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.strokes = []          # 確定したストロークのリスト
        self.current = None        # 描画中のストローク
        self.tool = "pen"
        self.color = nscolor(PRESET_COLORS[0])
        self.penWidth = 3.0
        return self

    # 座標を左上原点にして直感的に
    def isFlipped(self):
        return True

    def isOpaque(self):
        return False

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    # ---- 描画本体 ----
    def _make_path(self, s):
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(s["width"])
        path.setLineCapStyle_(NSRoundLineCapStyle)
        path.setLineJoinStyle_(NSRoundLineJoinStyle)
        pts = s["points"]
        tool = s["tool"]
        if not pts:
            return path
        if tool == "pen":
            path.moveToPoint_(pts[0])
            for p in pts[1:]:
                path.lineToPoint_(p)
        elif tool in ("line", "arrow"):
            path.moveToPoint_(pts[0])
            path.lineToPoint_(pts[-1])
        elif tool == "rect":
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            path.appendBezierPathWithRect_(
                NSMakeRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        elif tool == "ellipse":
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            path.appendBezierPathWithOvalInRect_(
                NSMakeRect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        return path

    def _draw_arrowhead(self, s):
        (x0, y0), (x1, y1) = s["points"][0], s["points"][-1]
        if (x0, y0) == (x1, y1):
            return
        head = max(12.0, s["width"] * 3.5)
        ang = math.atan2(y1 - y0, x1 - x0)
        a1, a2 = ang - math.pi / 6, ang + math.pi / 6
        p = NSBezierPath.bezierPath()
        p.moveToPoint_((x1, y1))
        p.lineToPoint_((x1 - head * math.cos(a1), y1 - head * math.sin(a1)))
        p.lineToPoint_((x1 - head * math.cos(a2), y1 - head * math.sin(a2)))
        p.closePath()
        p.fill()

    def _render(self, s):
        s["color"].setStroke()
        s["color"].setFill()
        self._make_path(s).stroke()
        if s["tool"] == "arrow":
            self._draw_arrowhead(s)

    def drawRect_(self, rect):
        for s in self.strokes:
            self._render(s)
        if self.current is not None:
            self._render(self.current)

    # ---- マウス操作 ----
    def _pt(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        return (p.x, p.y)

    def mouseDown_(self, event):
        self.current = {
            "tool": self.tool, "color": self.color,
            "width": self.penWidth, "points": [self._pt(event)],
        }
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        if self.current is None:
            return
        p = self._pt(event)
        if self.current["tool"] == "pen":
            self.current["points"].append(p)
        else:
            if len(self.current["points"]) == 1:
                self.current["points"].append(p)
            else:
                self.current["points"][-1] = p
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        if self.current is not None:
            self.strokes.append(self.current)
            self.current = None
            self.setNeedsDisplay_(True)

    # ---- 編集 ----
    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.setNeedsDisplay_(True)

    def clearAll(self):
        self.strokes = []
        self.current = None
        self.setNeedsDisplay_(True)


# ---------------------------------------------------------------------------
class OverlayWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True


# ---------------------------------------------------------------------------
class AppController(NSObject):

    def init(self):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.draw_mode = True
        self.tool_buttons = {}
        return self

    # ---- 構築 ----
    def build(self):
        frame = NSScreen.mainScreen().frame()

        # オーバーレイ本体
        self.window = OverlayWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setLevel_(NSStatusWindowLevel)
        self.window.setIgnoresMouseEvents_(False)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = OverlayView.alloc().initWithFrame_(frame)
        self.window.setContentView_(self.view)
        self.window.makeKeyAndOrderFront_(None)

        self._build_toolbar(frame)
        self._install_hotkeys()
        self._highlight_tool("pen")

    def _build_toolbar(self, frame):
        W, H = 760, 58
        x = (frame.size.width - W) / 2
        y = frame.size.height - H - 40
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskUtilityWindow
                 | NSWindowStyleMaskHUDWindow)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, W, H), style, NSBackingStoreBuffered, False)
        self.panel.setTitle_("FX描画")
        self.panel.setLevel_(NSStatusWindowLevel + 1)
        self.panel.setFloatingPanel_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary)
        content = self.panel.contentView()

        bx = 10
        by = 15
        bh = 28

        # モード切替
        self.mode_btn = self._button("● 描画中", bx, by, 78, bh, "toggleMode:", content)
        bx += 86

        # ツール
        for i, t in enumerate(TOOLS):
            b = self._button(TOOL_LABELS[t].split()[0], bx, by, 40, bh, "selectTool:", content)
            b.setTag_(i)
            self.tool_buttons[t] = b
            bx += 44
        bx += 6

        # 色スウォッチ
        for i, rgb in enumerate(PRESET_COLORS):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(bx, by, 26, bh))
            b.setTitle_("")
            b.setBezelStyle_(NSBezelStyleShadowlessSquare)
            b.setBordered_(True)
            b.setBezelColor_(nscolor(rgb))
            b.setTarget_(self)
            b.setAction_("selectColor:")
            b.setTag_(i)
            content.addSubview_(b)
            bx += 28
        bx += 6

        # 太さ
        self.slider = NSSlider.alloc().initWithFrame_(NSMakeRect(bx, by, 90, bh))
        self.slider.setMinValue_(1)
        self.slider.setMaxValue_(30)
        self.slider.setFloatValue_(3)
        self.slider.setTarget_(self)
        self.slider.setAction_("changeWidth:")
        content.addSubview_(self.slider)
        bx += 98

        # 編集・出力
        for label, act in [("↶", "undo:"), ("🗑", "clearAll:"),
                           ("💾", "save:"), ("✕", "quit:")]:
            self._button(label, bx, by, 36, bh, act, content)
            bx += 40

        self.panel.orderFrontRegardless()

    def _button(self, title, x, y, w, h, action, parent):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        b.setTitle_(title)
        b.setBezelStyle_(NSBezelStyleRounded)
        b.setTarget_(self)
        b.setAction_(action)
        parent.addSubview_(b)
        return b

    def _highlight_tool(self, tool):
        for t, b in self.tool_buttons.items():
            b.setBezelColor_(nscolor((0.29, 0.62, 1.00)) if t == tool else NSColor.controlColor())

    # ---- アクション ----
    def toggleMode_(self, sender):
        self.draw_mode = not self.draw_mode
        self.window.setIgnoresMouseEvents_(not self.draw_mode)
        if self.draw_mode:
            self.mode_btn.setTitle_("● 描画中")
            self.window.makeKeyAndOrderFront_(None)
            NSApp.activateIgnoringOtherApps_(True)
        else:
            self.mode_btn.setTitle_("○ 操作中")
        self.panel.orderFrontRegardless()

    def selectTool_(self, sender):
        t = TOOLS[sender.tag()]
        self.view.tool = t
        self._highlight_tool(t)

    def selectColor_(self, sender):
        self.view.color = nscolor(PRESET_COLORS[sender.tag()])

    def changeWidth_(self, sender):
        self.view.penWidth = float(sender.floatValue())

    def undo_(self, sender):
        self.view.undo()

    def clearAll_(self, sender):
        self.view.clearAll()

    def save_(self, sender):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.expanduser("~/Desktop/mt4-annotated-%s.png" % ts)
        # ツールバーを一瞬隠してから画面を撮影
        self.panel.orderOut_(None)
        self.performSelector_withObject_afterDelay_("doCapture:", path, 0.3)

    def doCapture_(self, path):
        try:
            subprocess.run(["screencapture", "-x", path], check=False)
        finally:
            self.panel.orderFrontRegardless()

    def quit_(self, sender):
        NSApp.terminate_(None)

    # ---- ショートカット（描画中に有効） ----
    def _install_hotkeys(self):
        def handler(event):
            chars = (event.charactersIgnoringModifiers() or "").lower()
            cmd = bool(event.modifierFlags() & NSEventModifierFlagCommand)
            if event.keyCode() == 100:            # F8
                self.toggleMode_(self.mode_btn)
                return None
            if cmd and chars == "z":
                self.view.undo()
                return None
            keymap = {"p": "pen", "l": "line", "a": "arrow", "r": "rect", "c": "ellipse"}
            if not cmd and chars in keymap:
                self.view.tool = keymap[chars]
                self._highlight_tool(keymap[chars])
                return None
            return event

        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, handler)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = AppController.alloc().init()
    controller.build()
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
