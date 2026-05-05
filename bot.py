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
import re

# Register HEIF opener for iPhone photo support
register_heif_opener()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ── Logging & Web Server ──────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "OK", 200

def run_web():
    # Flask must run on 0.0.0.0 for Koyeb health checks
    app.run(host='0.0.0.0', port=8000, use_reloader=False)

# ── Configuration ──────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Absolute path for Koyeb persistent volumes
DATA_FILE = "/app/data.json"

def escape_markdown(text):
    """Escapes characters that break Telegram's Markdown parser."""
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

# ── Image Processing ───────────────────────────────────────────────────────
def extract_metadata(image_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        meta = {
            "Basic Info": {
                "Format": img.format or "Unknown",
                "Dimensions": f"{img.width}x{img.height}",
                "Mode": img.mode
            }
        }
        
        try:
            exif_data = piexif.load(image_bytes)
            exif_sec = {}
            for ifd in ("0th", "Exif", "GPS"):
                for tag_id, value in exif_data.get(ifd, {}).items():
                    tag_name = piexif.TAGS.get(ifd, {}).get(tag_id, {}).get("name", f"Tag_{tag_id}")
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace").strip("\x00")
                    if value not in ("", b""):
                        exif_sec[tag_name] = str(value)
            if exif_sec:
                meta["EXIF Data"] = exif_sec
        except:
            pass
        return meta
    except Exception as e:
        return {"Error": {"Detail": str(e)}}

def format_report(meta: dict) -> str:
    lines = ["📸 *Photo Metadata Report*\n"]
    for section, fields in meta.items():
        lines.append(f"*{section}*")
        for k, v in fields.items():
            lines.append(f"• `{escape_markdown(k)}`: {escape_markdown(v)}")
        lines.append("")
    return "\n".join(lines)

# ── Handlers ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome to MetaSnap*\n\nSend any photo **as a file** (uncompressed) to see its hidden metadata.",
        parse_mode="Markdown"
    )

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Detect if it's a photo or a document
    is_doc = bool(update.message.document)
    file_obj = update.message.document if is_doc else update.message.photo[-1]
    
    msg = await update.message.reply_text("🔍 Processing image...")
    
    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        raw_bytes = bytes(await tg_file.download_as_bytearray())
        
        metadata = extract_metadata(raw_bytes)
        report = format_report(metadata)
        
        if not is_doc:
            report += "\n⚠️ _Note: This was sent as a photo. Metadata may be stripped by Telegram. Send as a **File** for full EXIF data._"
            
        await msg.delete()
        await update.message.reply_text(report, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
        await msg.edit_text("❌ An error occurred while parsing this file. Ensure it is a valid image format.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN not found in environment variables.")
        return

    # Start Flask thread for health checks
    threading.Thread(target=run_web, daemon=True).start()

    # Build Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Document.ALL, handle_image))
    application.add_error_handler(error_handler)

    print("🤖 MetaSnap Bot is starting...")
    
    # drop_pending_updates=True is critical to fix the loop you saw in logs
    application.run_polling(
        drop_pending_updates=True, 
        stop_signals=[signal.SIGINT, signal.SIGTERM]
    )

if __name__ == "__main__":
    main()
