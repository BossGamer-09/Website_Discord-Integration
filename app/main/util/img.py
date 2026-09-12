import functools
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from asgiref.sync import sync_to_async

_ORG_ASSETS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../org/static/assets')
)
_WELCOME_BG_PATH = os.path.join(_ORG_ASSETS, 'welcome_background.png')
_WELCOME_FONT_PATH = os.path.join(_ORG_ASSETS, 'NewRocker-Regular.ttf')


@functools.lru_cache(maxsize=8)
def _cached_image(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


@functools.lru_cache(maxsize=8)
def _cached_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _get_background(path: str) -> Image.Image:
    # .copy() so callers can't mutate the cached object
    return _cached_image(path).copy()


def add_stroke_masked(image, stroke_width, stroke_color):
    img = image.convert("RGBA")
    new_size = (img.width + 2 * stroke_width, img.height + 2 * stroke_width)
    canvas = Image.new("RGBA", new_size, (0, 0, 0, 0))
    mask_canvas = Image.new("L", new_size, 0)
    mask_canvas.paste(img.getchannel("A"), (stroke_width, stroke_width))
    stroke_mask = mask_canvas.filter(ImageFilter.MaxFilter(2 * stroke_width + 1))
    stroke_bg = Image.new("RGBA", new_size, stroke_color)
    canvas.paste(stroke_bg, (0, 0), mask=stroke_mask)
    canvas.paste(img, (stroke_width, stroke_width), mask=img)
    return canvas


async def _download_circular_avatar(user) -> Image.Image:
    avatar_bytes = None
    try:
        for size in [256, 128, 64]:
            asset = user.display_avatar.replace(size=size, format='png')
            try:
                avatar_bytes = BytesIO(await asset.read())
                break
            except Exception:
                continue
        if not avatar_bytes:
            avatar_bytes = BytesIO(await user.default_avatar.read())
        avatar = Image.open(avatar_bytes).convert("RGBA")
        mask = Image.new("L", avatar.size, 0)
        ImageDraw.Draw(mask).ellipse([(0, 0), avatar.size], fill=255)
        circular = ImageOps.fit(avatar, mask.size, centering=(0.5, 0.5))
        circular.putalpha(mask)
        return circular
    except Exception:
        fallback = Image.new("RGBA", (128, 128), (100, 100, 100, 255))
        ImageDraw.Draw(fallback).ellipse([10, 10, 118, 118], fill=(70, 70, 70, 255))
        return fallback


async def create_welcome_card(
    member,
    background_path: str = None,
    font_path: str = None,
    filename: str = 'welcome.png',
) -> "discord.File":
    import discord

    bg_path = background_path or _WELCOME_BG_PATH
    fnt_path = font_path or _WELCOME_FONT_PATH

    avatar = await _download_circular_avatar(member)

    def _render(avatar, member):
        try:
            background = _get_background(bg_path)
        except FileNotFoundError:
            background = Image.new("RGBA", (700, 250), (44, 47, 51, 255))
            ImageDraw.Draw(background).rectangle([20, 20, 680, 230], outline=(114, 137, 218, 255), width=5)

        if background.size != (700, 250):
            background = background.resize((700, 250), Image.Resampling.LANCZOS)

        canvas = Image.new("RGBA", (700, 250), (0, 0, 0, 0))
        canvas.paste(background, (0, 0))
        canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, 64)))

        avatar_size = 115
        avatar_resized = avatar.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
        avatar_resized = add_stroke_masked(avatar_resized, stroke_width=2, stroke_color=(100, 64, 109))

        avatar_x, avatar_y = 334, 105
        canvas.paste(avatar_resized, (avatar_x, avatar_y), avatar_resized)

        draw = ImageDraw.Draw(canvas)
        try:
            font = _cached_font(fnt_path, 27)
        except Exception:
            font = ImageFont.load_default()

        text_x = avatar_x + avatar_size + 41
        text_y = 110
        display_text = member.display_name or member.global_name or member.name
        for text, pos in [
            ("Welcome",       (text_x, text_y)),
            (display_text,    (text_x, text_y + 35)),
            ("to BlightVeil", (text_x, text_y + 70)),
        ]:
            draw.text(pos, text, font=font, fill=(255, 255, 255),
                      stroke_width=2, stroke_fill=(0, 0, 0))

        buf = BytesIO()
        canvas.save(buf, format='PNG')
        buf.seek(0)
        return buf

    img_bytes = await sync_to_async(_render)(avatar, member)
    return discord.File(img_bytes, filename=filename)
