import os
import io
import logging
import signal
import threading
import re
from flask import Flask
from PIL import Image
import piexif
from pillow_heif import register_heif_opener
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

register_heif_opener()

# ── Logging & Web Server ──────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health(): return "OK", 200

def run_web(): app.run(host='0.0.0.0', port=8000, use_reloader=False)

# ── Configuration ──────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

def escape_markdown(text):
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

# ── GPS Conversion Logic ──────────────────────────────────────────────────
def to_decimal(coords, ref):
    """Converts GPS rational tuples to decimal degrees."""
    try:
        deg = coords[0][0] / coords[0][1]
        mnt = coords[1][0] / coords[1][1]
        sec = coords[2][0] / coords[2][1]
        decimal = deg + (mnt / 60.0) + (sec / 3600.0)
        if ref in ['S', 'W']:
            decimal = -decimal
        return decimal
    except:
        return None

# ── Image Processing ───────────────────────────────────────────────────────
def extract_metadata(image_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        meta = {
            "Basic Info": {
                "Format": img.format or "Unknown",
                "Dimensions": f"{img.width}x{img.height}",
                "Mode": img.mode
            },
            "Location": {}
        }
        
        exif_data = piexif.load(image_bytes)
        
        # Parse GPS specifically
        gps = exif_data.get("GPS", {})
        if gps:
            lat = to_decimal(gps.get(piexif.GPSIFD.GPSLatitude), gps.get(piexif.GPSIFD.GPSLatitudeRef))
            lon = to_decimal(gps.get(piexif.GPSIFD.GPSLongitude), gps.get(piexif.GPSIFD.GPSLongitudeRef))
            if lat is not None and lon is not None:
                meta["Location"]["Coordinates"] = f"{lat:.6f}, {lon:.6f}"
                meta["Location"]["Google Maps"] = f"https://www.google.com/maps?q={lat},{lon}"

        # Parse General EXIF
        exif_sec = {}
        for ifd in ("0th", "Exif"):
            for tag_id, value in exif_data.get(ifd, {}).items():
                tag_name = piexif.TAGS.get(ifd, {}).get(tag_id, {}).get("name", f"Tag_{tag_id}")
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace").strip("\x00")
                if value not in ("", b"") and "Tag_" not in tag_name:
                    exif_sec[tag_name] = str(value)
        
        if exif_sec: meta["EXIF Data"] = exif_sec
        return meta
    except Exception as e:
        return {"Error": {"Detail": str(e)}}

def format_report(meta: dict) -> str:
    lines = ["📸 *Photo Metadata Report*\n"]
    
    # Prioritize Location at the top if it exists
    if meta.get("Location"):
        lines.append("*📍 Location*")
        for k, v in meta["Location"].items():
            lines.append(f"• {k}: {v}")
        lines.append("")

    for section, fields in meta.items():
        if section in ["Location", "Error"] or not fields: continue
        lines.append(f"*{section}*")
        for k, v in fields.items():
            lines.append(f"• `{escape_markdown(k)}`: {escape_markdown(v)}")
        lines.append("")
        
    if "Error" in meta:
        lines.append("❌ *Errors*: " + str(meta["Error"]))
        
    return "\n".join(lines)

# ── Handlers ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send a photo **as a file** to see metadata and map location.", parse_mode="Markdown")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_doc = bool(update.message.document)
    file_obj = update.message.document if is_doc else update.message.photo[-1]
    msg = await update.message.reply_text("🔍 Extracting metadata...")
    
    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        raw_bytes = bytes(await tg_file.download_as_bytearray())
        
        metadata = extract_metadata(raw_bytes)
        report = format_report(metadata)
        
        if not is_doc:
            report += "\n⚠️ _Note: Sent as compressed photo. Location data may be missing. Send as File for best results._"
            
        await msg.delete()
        await update.message.reply_text(report, parse_mode="Markdown", disable_web_page_preview=False)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await msg.edit_text("❌ Failed to parse file.")

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    threading.Thread(target=run_web, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Document.ALL, handle_image))
    
    logger.info("🤖 Bot is starting...")
    application.run_polling(drop_pending_updates=True, stop_signals=[signal.SIGINT, signal.SIGTERM])

if __name__ == "__main__":
    main()
