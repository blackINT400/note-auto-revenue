"""
note記事ヘッダー画像生成ツール
usage: python3 tools/gen_image.py
"""
from PIL import Image, ImageDraw, ImageFont
import math, os, sys

OUTPUT_PATH = "tools/output/article_header.png"
W, H = 1280, 670

# ── カラーパレット ────────────────────────────────────────
BG_TOP    = (10, 10, 18)
BG_BOT    = (15, 23, 42)
ACCENT    = (99, 102, 241)       # indigo-500
ACCENT_LT = (129, 140, 248)      # indigo-400
TEXT_PRI  = (241, 245, 249)      # slate-100
TEXT_SEC  = (148, 163, 184)      # slate-400
TEXT_DIM  = (71,  85, 105)       # slate-600
GRID_COL  = (59, 130, 246, 8)    # blue-500 very faint


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def draw_gradient_bg(img):
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = lerp_color(BG_TOP, BG_BOT, t)
        draw.line([(0, y), (W, y)], fill=c)


def draw_grid(img):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    step = 60
    color = (59, 130, 246, 10)
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=color, width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=color, width=1)
    img.paste(overlay, mask=overlay.split()[3])


def draw_glow(img, cx, cy, radius, color_rgb, alpha_max=40):
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    steps = 30
    for i in range(steps, 0, -1):
        r = int(radius * i / steps)
        alpha = int(alpha_max * (1 - i / steps) ** 0.5)
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  fill=(*color_rgb, alpha))
    img.paste(overlay, mask=overlay.split()[3])


def find_font(size):
    """利用可能な日本語フォントを探す"""
    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    """テキストを指定幅で折り返す"""
    lines = []
    current = ""
    for ch in text:
        test = current + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def generate(
    title_main="嫌悪感は境界線の言語である",
    title_sub="——他者の身体が不快なとき、あなたは何を守っている",
    tagline="感情ではない。センサーの出力だ。",
    label="全1論 × 人間関係",
    author="ミリテク",
    author_tag="言語化の技術 / 全1論",
    output=OUTPUT_PATH,
):
    os.makedirs(os.path.dirname(output), exist_ok=True)

    img = Image.new("RGB", (W, H))

    # 背景グラデーション
    draw_gradient_bg(img)
    draw_grid(img)

    # グロー（右上 + 左下）
    draw_glow(img, W - 150, -100, 500, (99, 102, 241), alpha_max=35)
    draw_glow(img, 0, H + 50, 400, (99, 102, 241), alpha_max=20)

    draw = ImageDraw.Draw(img)

    # フォント
    font_label  = find_font(14)
    font_main   = find_font(54)
    font_sub    = find_font(32)
    font_tag    = find_font(20)
    font_author = find_font(17)
    font_mini   = find_font(13)

    px, py = 90, 90

    # アクセントライン
    draw.rectangle([px, py, px + 56, py + 3], fill=ACCENT)
    py += 24

    # ラベル
    draw.text((px, py), label, font=font_label, fill=ACCENT_LT)
    py += 44

    # メインタイトル（折り返し対応）
    main_lines = wrap_text(draw, title_main, font_main, W - px * 2)
    for line in main_lines:
        draw.text((px, py), line, font=font_main, fill=TEXT_PRI)
        py += 68

    py += 6

    # サブタイトル
    sub_lines = wrap_text(draw, title_sub, font_sub, W - px * 2)
    for line in sub_lines:
        draw.text((px, py), line, font=font_sub, fill=TEXT_SEC)
        py += 44

    py += 20

    # タグライン（細い横線 + テキスト）
    draw.rectangle([px, py, px + 30, py + 1], fill=(71, 85, 105))
    draw.text((px + 40, py - 8), tagline, font=font_tag, fill=TEXT_DIM)

    # 著者（右下）
    author_x = W - 220
    author_y = H - 80
    draw.text((author_x, author_y), author, font=font_author, fill=(100, 116, 139))
    draw.text((author_x, author_y + 22), author_tag, font=font_mini, fill=(51, 65, 85))

    # 右下にうっすらロゴ文字
    draw.text((W - 85, H - 35), "note.com", font=font_mini, fill=(30, 41, 59))

    img.save(output, "PNG", optimize=True)
    print(f"Saved: {output}  ({W}x{H}px)")
    return output


if __name__ == "__main__":
    # コマンドライン引数でタイトル上書き可能
    generate()
