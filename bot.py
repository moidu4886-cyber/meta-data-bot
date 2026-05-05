"""
MetaSnap Bot — Full Edition (Fixed for Koyeb Deployment)
"""

import os
import io
import json
import signal
import logging
import datetime
import cv2
import numpy as np
from PIL import Image
import piexif
from flask import Flask
import threading
from pillow_heif import register_heif_opener

# Register HEIF opener for iPhone photo support
register_heif_opener()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    # Flask must run on 0.0.0.0 for Koyeb health checks to pass
    app.run(host='0.0.0.0', port=8000, use_reloader=False)

# ── Config from environment ───────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = set(
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
)

# Use absolute path for Koyeb persistent volumes
DATA_FILE = "/app/data.json"

# ── Conversation states ───────────────────────────────────────────────────────
(
    EDIT_CHOOSING_FIELD,
    EDIT_TYPING_VALUE,
    BROADCAST_TYPING,
    POLL_TITLE,
    POLL_OPTIONS,
    FEEDBACK_TYPING,
    RESIZE_TYPING,
) = range(7)

# ── Persistent data store ─────────────────────────────────────────────────────
def _load() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load error: {e}")
    return {"users": {}, "stats": {"total_scans": 0, "total_strips": 0, "total_edits": 0}}

def _save(db: dict) -> None:
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

DB = _load()

def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

def get_user(uid: int) -> dict:
    key = str(uid)
    if key not in DB["users"]:
        DB["users"][key] = {
            "name": "", "username": "",
            "first_seen": _now(), "last_seen": _now(),
            "scans": 0, "strips": 0, "edits": 0,
            "banned": False, "history": [],
        }
    return DB["users"][key]

def bump_stat(uid: int, field: str) -> None:
    u = get_user(uid)
    u[field] = u.get(field, 0) + 1
    u["last_seen"] = _now()
    DB["stats"][f"total_{field}"] = DB["stats"].get(f"total_{field}", 0) + 1
    _save(DB)

def is_banned(uid: int) -> bool:
    return get_user(uid).get("banned", False)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ── Guards ────────────────────────────────────────────────────────────────────
def require_not_banned(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if is_banned(uid):
            await update.effective_message.reply_text("🚫 You are banned from using this bot.")
            return
        u = get_user(uid)
        u["name"]     = update.effective_user.full_name or ""
        u["username"] = update.effective_user.username or ""
        u["last_seen"] = _now()
        _save(DB)
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.effective_message.reply_text("⛔ Admin only.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

# ── Image helpers ─────────────────────────────────────────────────────────────
def bytes_to_human(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

def extract_metadata(image_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return {"Basic Info": {"Error": "Could not open image file."}}

    meta = {
        "Basic Info": {
            "Format":     img.format or "Unknown",
            "Mode":       img.mode,
            "Width":      f"{img.width} px",
            "Height":     f"{img.height} px",
            "Megapixels": f"{img.width * img.height / 1_000_000:.2f} MP",
            "Size":       bytes_to_human(len(image_bytes)),
        }
    }

    try:
        exif_data = piexif.load(image_bytes)
        exif_sec: dict = {}
        for ifd in ("0th", "Exif", "GPS", "1st"):
            for tag_id, value in exif_data.get(ifd, {}).items():
                tag_name = piexif.TAGS.get(ifd, {}).get(tag_id, {}).get("name", f"Tag_{tag_id}")
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace").strip("\x00")
                elif isinstance(value, tuple) and len(value) == 2:
                    value = round(value[0] / value[1], 6) if value[1] else str(value)
                if tag_name and value not in ("", b""):
                    exif_sec[tag_name] = str(value)

        gps = exif_data.get("GPS", {})
        if gps:
            try:
                def dms(v, ref):
                    d, m, s = [x[0] / x[1] for x in v]
                    dec = d + m / 60 + s / 3600
                    return -dec if ref in (b"S", b"W") else dec
                lat = dms(gps[piexif.GPSIFD.GPSLatitude],  gps.get(piexif.GPSIFD.GPSLatitudeRef))
                lon = dms(gps[piexif.GPSIFD.GPSLongitude], gps.get(piexif.GPSIFD.GPSLongitudeRef))
                exif_sec["GPS Latitude"]  = str(round(lat, 7))
                exif_sec["GPS Longitude"] = str(round(lon, 7))
                exif_sec["📍 Maps Link"]  = f"https://maps.google.com/?q={round(lat,7)},{round(lon,7)}"
            except Exception:
                pass

        if exif_sec:
            meta["EXIF Data"] = exif_sec
    except Exception:
        pass

    return meta

def format_metadata(meta: dict) -> str:
    lines = ["📸 *Photo Metadata Report*\n"]
    for section, fields in meta.items():
        lines.append(f"*── {section} ──*")
        for k, v in fields.items():
            sv = str(v).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            lines.append(f"  `{k}`: {sv}" if k != "📍 Maps Link" else f"  {k}: {sv}")
        lines.append("")
    return "\n".join(lines)

def strip_metadata(raw: bytes, fmt: str) -> bytes:
    img = Image.open(io.BytesIO(raw))
    out = io.BytesIO()
    if img.mode in ("RGBA", "P") and fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(out, format=fmt or "JPEG")
    return out.getvalue()

def resize_image(raw: bytes, w: int, h: int, fmt: str) -> bytes:
    img = Image.open(io.BytesIO(raw)).resize((w, h), Image.LANCZOS)
    out = io.BytesIO()
    if img.mode in ("RGBA", "P") and fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(out, format=fmt or "JPEG")
    return out.getvalue()

def convert_format(raw: bytes, target_fmt: str) -> bytes:
    img = Image.open(io.BytesIO(raw))
    out = io.BytesIO()
    if img.mode in ("RGBA", "P") and target_fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(out, format=target_fmt)
    return out.getvalue()

def edit_exif_field(raw: bytes, field: str, value: str) -> bytes:
    img = Image.open(io.BytesIO(raw))
    try:
        exif_dict = piexif.load(raw)
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    
    field_map = {
        "author":      (piexif.ImageIFD.Artist,           "0th"),
        "copyright":   (piexif.ImageIFD.Copyright,        "0th"),
        "description": (piexif.ImageIFD.ImageDescription, "0th"),
        "software":    (piexif.ImageIFD.Software,         "0th"),
        "datetime":    (piexif.ImageIFD.DateTime,         "0th"),
        "comment":     (piexif.ExifIFD.UserComment,       "Exif"),
    }
    
    tag_id, ifd = field_map.get(field.lower(), (None, None))
    if tag_id and ifd:
        exif_dict.setdefault(ifd, {})[tag_id] = value.encode()
        
    try:
        exif_bytes = piexif.dump(exif_dict)
    except Exception:
        exif_bytes = b""
        
    out = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    if exif_bytes:
        img.save(out, format="JPEG", exif=exif_bytes)
    else:
        img.save(out, format="JPEG")
        
    return out.getvalue()

# ── Image Authenticity Logic ──────────────────────────────────────────────────
def analyze_authenticity(image_bytes: bytes) -> dict:
    risk_score = 0
    reasons = []

    # 1. Pilot decoding (Handles HEIC via registered opener)
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Convert to RGB to ensure OpenCV can handle the pixel data from memory
        img_rgb = img.convert("RGB")
        cv_img = cv2.cvtColor(np.array(img_rgb), cv2.COLOR_RGB2GRAY)
    except Exception as e:
        return {"score": 100, "verdict": "🔴 Decoding Error", "reasons": [f"Analysis failed: {str(e)}"]}

    # 2. Metadata Analysis
    software_used = None
    try:
        exif_data = piexif.load(image_bytes)
        if not any(exif_data.values()):
            risk_score += 25
            reasons.append("No EXIF metadata (metadata wiped or natively lacks it)")
        else:
            sw = exif_data.get("0th", {}).get(piexif.ImageIFD.Software, b"")
            if sw: software_used = sw.decode("utf-8", errors="ignore").strip()
    except Exception:
        risk_score += 35
        reasons.append("EXIF metadata structure is non-standard/corrupted")

    if software_used:
        known_editors = ["photoshop", "snapseed", "picsart", "canva", "lightroom", "gimp", "illustrator"]
        if any(ed in software_used.lower() for ed in known_editors):
            risk_score += 65
            reasons.append(f"Edited using known software: {software_used}")
        else:
            reasons.append(f"Software tag found: {software_used}")

    # 3. Laplacian Variance (Sharpness/Noise Analysis)
    if cv_img is not None:
        variance = cv2.Laplacian(cv_img, cv2.CV_64F).var()
        if variance < 80:
            risk_score += 20
            reasons.append(f"Low edge detail ({variance:.1f}) — possible re-compression or blur")

    risk_score = min(risk_score, 100)
    verdict = "🟢 Likely Original" if risk_score < 30 else "🟡 Possibly Edited" if risk_score < 60 else "🔴 Likely Edited"

    return {"score": risk_score, "verdict": verdict, "reasons": reasons if reasons else ["No suspicious signs"]}

def action_keyboard(file_id: str, fmt: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Strip Metadata",  callback_data=f"strip:{file_id}:{fmt}"),
            InlineKeyboardButton("✏️ Edit EXIF",       callback_data=f"editstart:{file_id}:{fmt}"),
        ],
        [
            InlineKeyboardButton("📊 Compare Size",    callback_data=f"compare:{file_id}:{fmt}"),
            InlineKeyboardButton("📐 Resize",          callback_data=f"resizeprompt:{file_id}:{fmt}"),
        ],
        [
            InlineKeyboardButton("🔄 Convert Format",  callback_data=f"convertmenu:{file_id}:{fmt}"),
            InlineKeyboardButton("🔍 Analyze",         callback_data=f"analyze:{file_id}:{fmt}"),
        ]
    ])

# ── Handlers ──────────────────────────────────────────────────────────────────
@require_not_banned
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to MetaSnap Bot!*\n\n"
        "📸 Send any photo or image *as a file* to extract full metadata.\n"
        "I support JPG, PNG, WEBP, and iPhone HEIC files.",
        parse_mode="Markdown",
    )

@require_not_banned
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg   = await update.message.reply_text("🔍 Analysing photo…")
    photo = update.message.photo[-1]
    file  = await context.bot.get_file(photo.file_id)
    raw   = bytes(await file.download_as_bytearray())
    meta  = extract_metadata(raw)
    bump_stat(update.effective_user.id, "scans")
    await msg.delete()
    await update.message.reply_text(
        format_metadata(meta) + "\n⚠️ _Compressed by Telegram — send as file for full EXIF._",
        parse_mode="Markdown",
        reply_markup=action_keyboard(photo.file_id, "JPEG"),
    )

@require_not_banned
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    # Better HEIC support check
    ext = doc.file_name.lower().split('.')[-1]
    if ext not in ['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'tiff']:
        await update.message.reply_text("❌ Unsupported image format.")
        return

    msg = await update.message.reply_text("🔍 Analysing image…")
    file = await context.bot.get_file(doc.file_id)
    raw  = bytes(await file.download_as_bytearray())
    fmt  = "JPEG" if ext in ['jpg', 'jpeg', 'heic', 'heif'] else ext.upper()

    meta = extract_metadata(raw)
    bump_stat(update.effective_user.id, "scans")
    await msg.delete()
    await update.message.reply_text(
        format_metadata(meta),
        parse_mode="Markdown",
        reply_markup=action_keyboard(doc.file_id, fmt),
    )

async def cb_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, file_id, _ = q.data.split(":", 2)
    msg = await q.message.reply_text("🔍 Running deep analysis...")
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    res  = analyze_authenticity(raw)
    reasons = "\n".join([f"• {r}" for r in res['reasons']])
    await msg.edit_text(
        f"📊 *Authenticity Report*\n\nVerdict: {res['verdict']}\nScore: `{res['score']}/100`\n\n*Reasons:*\n{reasons}",
        parse_mode="Markdown"
    )

async def cb_strip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, file_id, fmt = q.data.split(":", 2)
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    clean = strip_metadata(raw, fmt)
    bio = io.BytesIO(clean)
    bio.name = f"clean.{fmt.lower()}"
    bump_stat(q.from_user.id, "strips")
    await q.message.reply_document(document=bio, caption="✅ Metadata stripped.")

# (Other callback handlers: cb_compare, cb_convert, etc. remain similar to previous logic)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ An error occurred. Try /start.")

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing!")

    # Start Flask for Koyeb Health Checks
    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Handle documents with specific image extensions
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    app.add_handler(CallbackQueryHandler(cb_analyze, pattern=r"^analyze:"))
    app.add_handler(CallbackQueryHandler(cb_strip,   pattern=r"^strip:"))
    
    app.add_error_handler(error_handler)

    logger.info("🤖 Bot is online.")
    app.run_polling(drop_pending_updates=True, stop_signals=[signal.SIGINT, signal.SIGTERM])

if __name__ == "__main__":
    main()
