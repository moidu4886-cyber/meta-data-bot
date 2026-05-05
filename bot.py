"""
MetaSnap Bot — Full Edition
Features:
  • Photo metadata extraction (EXIF, GPS, ICC, DPI, etc.)
  • Metadata stripping + before/after size comparison
  • EXIF field editor (Author, Copyright, Description, Software, DateTime, Comment)
  • Resize image to custom dimensions
  • Convert format (JPEG ↔ PNG ↔ WEBP)
  • User scan history (last 5 scans per user)
  • /poll — create Telegram polls in-chat
  • /feedback — users send feedback to admin
  • Admin: /stats /users /ban /unban /broadcast /adminhelp
  • Auto-notify admin on new user
  • Graceful shutdown on SIGINT / SIGTERM
  • Ban guard on all user commands
  • Persistent JSON datastore (data.json)
"""

import os
import io
import json
import signal
import logging
import datetime
from PIL import Image
import piexif

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

# ── Config from environment ───────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = set(
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
)
DATA_FILE = "data.json"

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
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "stats": {"total_scans": 0, "total_strips": 0, "total_edits": 0}}

def _save(db: dict) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(db, f, indent=2)

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
    img  = Image.open(io.BytesIO(image_bytes))
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

    if img.info.get("icc_profile"):
        meta["Color Profile"] = {"ICC Profile": "Present"}

    extra: dict = {}
    skip = {"exif","icc_profile","jfif","jfif_version","jfif_unit","jfif_density","dpi","comment"}
    for k, v in (img.info or {}).items():
        if k.lower() not in skip and not isinstance(v, bytes):
            extra[k] = str(v)
    if "dpi" in img.info:
        dpi = img.info["dpi"]
        extra["DPI"] = f"{dpi[0]} x {dpi[1]}"
    if extra:
        meta["Image Info"] = extra

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
    exif_bytes = piexif.dump(exif_dict)
    out = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(out, format="JPEG", exif=exif_bytes)
    return out.getvalue()

def metadata_summary(meta: dict) -> str:
    b   = meta.get("Basic Info", {})
    gps = "🌍GPS" if "📍 Maps Link" in meta.get("EXIF Data", {}) else ""
    return f"{b.get('Format','?')} {b.get('Width','?')}×{b.get('Height','?')} {b.get('Size','?')} {gps}".strip()

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
        ],
    ])

# ── /start ────────────────────────────────────────────────────────────────────
@require_not_banned
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    user = get_user(uid)
    new  = user["scans"] == 0 and user["strips"] == 0

    await update.message.reply_text(
        "👋 *Welcome to MetaSnap Bot!*\n\n"
        "📸 Send any photo or image *as a file* to extract full metadata.\n\n"
        "*Commands:*\n"
        "  /help — usage guide\n"
        "  /history — your last 5 scans\n"
        "  /poll — create a Telegram poll\n"
        "  /feedback — send feedback to admin\n"
        "  /cancel — cancel current operation\n\n"
        "💡 _Tip: Send as 📎 file to preserve full EXIF / GPS data_",
        parse_mode="Markdown",
    )

    if new:
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    f"🆕 *New user!*\n"
                    f"Name: {update.effective_user.full_name}\n"
                    f"Username: @{update.effective_user.username or 'N/A'}\n"
                    f"ID: `{uid}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

# ── /help ─────────────────────────────────────────────────────────────────────
@require_not_banned
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *MetaSnap Help*\n\n"
        "*📸 Scan a photo*\n"
        "  Send any photo or image file → full metadata report\n"
        "  Send as 📎 file to keep GPS / camera model / EXIF intact\n\n"
        "*🛠 Action buttons (appear after every scan)*\n"
        "  🗑 Strip Metadata — remove all EXIF and save clean copy\n"
        "  ✏️ Edit EXIF — change Author, Copyright, DateTime etc.\n"
        "  📊 Compare Size — before vs after strip report\n"
        "  📐 Resize — enter custom width & height\n"
        "  🔄 Convert Format — JPEG ↔ PNG ↔ WEBP\n\n"
        "*🗳 Other features*\n"
        "  /poll — create a Telegram poll step by step\n"
        "  /feedback — send a message to the admin\n"
        "  /history — your last 5 scans\n"
        "  /cancel — exit any active operation\n\n"
        "*⚙️ Admin (admin-only)*\n"
        "  /stats /users /ban /unban /broadcast /adminhelp",
        parse_mode="Markdown",
    )

# ── /history ──────────────────────────────────────────────────────────────────
@require_not_banned
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hist = get_user(update.effective_user.id).get("history", [])
    if not hist:
        await update.message.reply_text("📭 No scan history yet. Send a photo to start!")
        return
    lines = ["📋 *Your last scans:*\n"]
    for i, h in enumerate(reversed(hist), 1):
        lines.append(f"{i}. {h}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── Photo / Document handlers ─────────────────────────────────────────────────
@require_not_banned
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg   = await update.message.reply_text("🔍 Analysing photo…")
    uid   = update.effective_user.id
    photo = update.message.photo[-1]
    file  = await context.bot.get_file(photo.file_id)
    raw   = bytes(await file.download_as_bytearray())
    meta  = extract_metadata(raw)

    u = get_user(uid)
    u.setdefault("history", []).append(metadata_summary(meta))
    u["history"] = u["history"][-5:]
    bump_stat(uid, "scans")

    await msg.delete()
    await update.message.reply_text(
        format_metadata(meta) + "\n⚠️ _Telegram compressed this photo — send as 📎 file for full EXIF._",
        parse_mode="Markdown",
        reply_markup=action_keyboard(photo.file_id, "JPEG"),
    )

@require_not_banned
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc  = update.message.document
    mime = doc.mime_type or ""
    if not mime.startswith("image/"):
        await update.message.reply_text("❌ Please send an image file (JPG, PNG, TIFF, WEBP…)")
        return

    msg = await update.message.reply_text("🔍 Analysing image…")
    uid = update.effective_user.id
    file = await context.bot.get_file(doc.file_id)
    raw  = bytes(await file.download_as_bytearray())
    fmt  = mime.split("/")[1].upper()
    if fmt == "JPG":
        fmt = "JPEG"

    meta = extract_metadata(raw)
    u = get_user(uid)
    u.setdefault("history", []).append(metadata_summary(meta))
    u["history"] = u["history"][-5:]
    bump_stat(uid, "scans")

    await msg.delete()
    await update.message.reply_text(
        format_metadata(meta),
        parse_mode="Markdown",
        reply_markup=action_keyboard(doc.file_id, fmt),
    )

# ── Strip ─────────────────────────────────────────────────────────────────────
async def cb_strip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, file_id, fmt = q.data.split(":", 2)
    uid = q.from_user.id
    msg = await q.message.reply_text("⚙️ Stripping metadata…")
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    clean = strip_metadata(raw, fmt)
    saved = len(raw) - len(clean)
    ext = "jpg" if fmt.upper() == "JPEG" else fmt.lower()
    bio = io.BytesIO(clean)
    bio.name = f"clean.{ext}"
    bump_stat(uid, "strips")
    await msg.delete()
    await q.message.reply_document(
        document=bio, filename=bio.name,
        caption=(
            f"✅ *Metadata stripped!*\n"
            f"Original: `{bytes_to_human(len(raw))}`\n"
            f"Clean:    `{bytes_to_human(len(clean))}`\n"
            f"Saved:    `{bytes_to_human(saved)}`"
        ),
        parse_mode="Markdown",
    )

# ── Compare ───────────────────────────────────────────────────────────────────
async def cb_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, file_id, fmt = q.data.split(":", 2)
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    clean = strip_metadata(raw, fmt)
    orig_fields  = sum(len(v) for v in extract_metadata(raw).values())
    clean_fields = sum(len(v) for v in extract_metadata(clean).values())
    await q.message.reply_text(
        "📊 *Before vs After Strip*\n\n"
        f"📦 Size:    `{bytes_to_human(len(raw))}` → `{bytes_to_human(len(clean))}`\n"
        f"🏷 Fields:  `{orig_fields}` → `{clean_fields}`\n"
        f"💾 Saved:   `{bytes_to_human(len(raw) - len(clean))}`",
        parse_mode="Markdown",
    )

# ── Convert ───────────────────────────────────────────────────────────────────
async def cb_convert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, file_id, fmt = q.data.split(":", 2)
    await q.message.reply_text(
        "🔄 *Choose output format:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("→ JPEG", callback_data=f"doconvert:{file_id}:{fmt}:JPEG"),
                InlineKeyboardButton("→ PNG",  callback_data=f"doconvert:{file_id}:{fmt}:PNG"),
                InlineKeyboardButton("→ WEBP", callback_data=f"doconvert:{file_id}:{fmt}:WEBP"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="noop")],
        ]),
    )

async def cb_do_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")  # doconvert:fid:src:tgt
    file_id, tgt_fmt = parts[1], parts[3]
    msg  = await q.message.reply_text(f"⚙️ Converting to {tgt_fmt}…")
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    out  = convert_format(raw, tgt_fmt)
    ext  = "jpg" if tgt_fmt == "JPEG" else tgt_fmt.lower()
    bio  = io.BytesIO(out)
    bio.name = f"converted.{ext}"
    await msg.delete()
    await q.message.reply_document(
        document=bio, filename=bio.name,
        caption=f"✅ Converted to *{tgt_fmt}* — `{bytes_to_human(len(out))}`",
        parse_mode="Markdown",
    )

# ── Resize (via button) ───────────────────────────────────────────────────────
async def cb_resize_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    _, file_id, fmt = q.data.split(":", 2)
    context.user_data["resize_file_id"] = file_id
    context.user_data["resize_fmt"]     = fmt
    await q.message.reply_text(
        "📐 *Resize Image*\n\nSend dimensions like `800 600` or `1280x720`.\n\n/cancel to abort.",
        parse_mode="Markdown",
    )
    return RESIZE_TYPING

async def resize_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text    = update.message.text.strip().replace("x", " ").replace("×", " ")
    parts   = text.split()
    file_id = context.user_data.get("resize_file_id")
    fmt     = context.user_data.get("resize_fmt", "JPEG")
    if not file_id or len(parts) != 2:
        await update.message.reply_text("❌ Use format `800 600`. /cancel to abort.", parse_mode="Markdown")
        return RESIZE_TYPING
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ Numbers only.")
        return RESIZE_TYPING
    if not (10 <= w <= 8000 and 10 <= h <= 8000):
        await update.message.reply_text("❌ Dimensions must be between 10 and 8000 px.")
        return RESIZE_TYPING
    msg  = await update.message.reply_text(f"⚙️ Resizing to {w}×{h}…")
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    out  = resize_image(raw, w, h, fmt)
    ext  = "jpg" if fmt.upper() == "JPEG" else fmt.lower()
    bio  = io.BytesIO(out)
    bio.name = f"resized_{w}x{h}.{ext}"
    await msg.delete()
    await update.message.reply_document(
        document=bio, filename=bio.name,
        caption=f"✅ Resized to *{w}×{h}* — `{bytes_to_human(len(out))}`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

# ── Edit EXIF conversation ────────────────────────────────────────────────────
EDITABLE_FIELDS = ["author", "copyright", "description", "software", "datetime", "comment"]

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    _, file_id, fmt = q.data.split(":", 2)
    context.user_data["edit_file_id"] = file_id
    context.user_data["edit_fmt"]     = fmt
    buttons = [
        [InlineKeyboardButton(f.capitalize(), callback_data=f"editfield:{f}")]
        for f in EDITABLE_FIELDS
    ]
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="editcancel")])
    await q.message.reply_text(
        "✏️ *Edit EXIF Field*\nChoose which field to edit:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return EDIT_CHOOSING_FIELD

async def edit_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    field = q.data.split(":")[1]
    context.user_data["edit_field"] = field
    await q.message.reply_text(
        f"✏️ Enter new value for *{field.capitalize()}*:\n(Send /cancel to abort)",
        parse_mode="Markdown",
    )
    return EDIT_TYPING_VALUE

async def edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value   = update.message.text.strip()
    file_id = context.user_data.get("edit_file_id")
    fmt     = context.user_data.get("edit_fmt", "JPEG")
    field   = context.user_data.get("edit_field")
    msg = await update.message.reply_text(f"⚙️ Writing `{field}` = `{value}`…", parse_mode="Markdown")
    file = await context.bot.get_file(file_id)
    raw  = bytes(await file.download_as_bytearray())
    try:
        out = edit_exif_field(raw, field, value)
    except Exception as e:
        await msg.edit_text(f"❌ Failed: {e}")
        return ConversationHandler.END
    bio = io.BytesIO(out)
    bio.name = "edited.jpg"
    bump_stat(update.effective_user.id, "edits")
    await msg.delete()
    await update.message.reply_document(
        document=bio, filename=bio.name,
        caption=f"✅ *{field.capitalize()}* set to: `{value}`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()
        await q.message.reply_text("✖️ Edit cancelled.")
    else:
        await update.message.reply_text("✖️ Edit cancelled.")
    return ConversationHandler.END

# ── /poll conversation ────────────────────────────────────────────────────────
@require_not_banned
async def poll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📊 *Create a Poll*\n\nStep 1 — Send the *question*.\n\n/cancel to abort.",
        parse_mode="Markdown",
    )
    return POLL_TITLE

async def poll_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["poll_question"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Question saved!\n\nStep 2 — Send *options*, one per line (2–10).\n\n/cancel to abort.",
        parse_mode="Markdown",
    )
    return POLL_OPTIONS

async def poll_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    opts = [o.strip() for o in update.message.text.strip().split("\n") if o.strip()]
    if len(opts) < 2:
        await update.message.reply_text("❌ At least 2 options needed.")
        return POLL_OPTIONS
    if len(opts) > 10:
        await update.message.reply_text("❌ Max 10 options.")
        return POLL_OPTIONS
    question = context.user_data.get("poll_question", "Poll")
    await update.message.reply_poll(question=question, options=opts, is_anonymous=True)
    return ConversationHandler.END

# ── /feedback conversation ────────────────────────────────────────────────────
@require_not_banned
async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "💬 *Send Feedback*\n\nType your message — it will be forwarded to the admin.\n\n/cancel to abort.",
        parse_mode="Markdown",
    )
    return FEEDBACK_TYPING

async def feedback_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = update.message.text.strip()
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(
                aid,
                f"📩 *Feedback* from [{user.full_name}](tg://user?id={user.id}) (`{user.id}`):\n\n{text}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    await update.message.reply_text("✅ Feedback sent. Thank you!")
    return ConversationHandler.END

# ── /cancel ───────────────────────────────────────────────────────────────────
async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("✖️ Operation cancelled.")
    return ConversationHandler.END

# ══ ADMIN COMMANDS ════════════════════════════════════════════════════════════

@require_admin
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats  = DB.get("stats", {})
    users  = DB.get("users", {})
    total  = len(users)
    banned = sum(1 for u in users.values() if u.get("banned"))
    active = sum(1 for u in users.values() if u.get("scans", 0) > 0)
    await update.message.reply_text(
        "📊 *Bot Statistics*\n\n"
        f"👥 Total users:  `{total}`\n"
        f"✅ Active users: `{active}`\n"
        f"🚫 Banned:       `{banned}`\n\n"
        f"📸 Total scans:  `{stats.get('total_scans', 0)}`\n"
        f"🗑 Total strips: `{stats.get('total_strips', 0)}`\n"
        f"✏️ Total edits:  `{stats.get('total_edits', 0)}`",
        parse_mode="Markdown",
    )

@require_admin
async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = DB.get("users", {})
    if not users:
        await update.message.reply_text("No users yet.")
        return
    lines = ["👥 *User List* (max 50)\n"]
    for uid, u in list(users.items())[:50]:
        ban  = " 🚫" if u.get("banned") else ""
        name = u.get("name", "?")
        un   = f"@{u['username']}" if u.get("username") else ""
        lines.append(
            f"• `{uid}` {name} {un}{ban}\n"
            f"   📸{u.get('scans',0)} 🗑{u.get('strips',0)} ✏️{u.get('edits',0)} "
            f"| {u.get('first_seen','?')}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

@require_admin
async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /ban `<user_id>`", parse_mode="Markdown")
        return
    uid = context.args[0]
    u = DB["users"].get(uid)
    if not u:
        await update.message.reply_text(f"User `{uid}` not found.", parse_mode="Markdown")
        return
    u["banned"] = True
    _save(DB)
    await update.message.reply_text(f"🚫 User `{uid}` banned.", parse_mode="Markdown")
    try:
        await context.bot.send_message(int(uid), "🚫 You have been banned from MetaSnap Bot.")
    except Exception:
        pass

@require_admin
async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unban `<user_id>`", parse_mode="Markdown")
        return
    uid = context.args[0]
    u = DB["users"].get(uid)
    if not u:
        await update.message.reply_text(f"User `{uid}` not found.", parse_mode="Markdown")
        return
    u["banned"] = False
    _save(DB)
    await update.message.reply_text(f"✅ User `{uid}` unbanned.", parse_mode="Markdown")
    try:
        await context.bot.send_message(int(uid), "✅ You have been unbanned. Welcome back!")
    except Exception:
        pass

@require_admin
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚙️ *Admin Commands*\n\n"
        "/stats — usage statistics\n"
        "/users — list all users (max 50)\n"
        "/ban `<uid>` — ban a user\n"
        "/unban `<uid>` — unban a user\n"
        "/broadcast — send message to all users\n"
        "/adminhelp — this message",
        parse_mode="Markdown",
    )

# ── Broadcast conversation ────────────────────────────────────────────────────
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return ConversationHandler.END
    await update.message.reply_text(
        "📢 *Broadcast*\n\nType the message to send to ALL users.\n\n/cancel to abort.",
        parse_mode="Markdown",
    )
    return BROADCAST_TYPING

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text  = update.message.text.strip()
    users = DB.get("users", {})
    sent  = failed = 0
    status = await update.message.reply_text(f"📤 Sending to {len(users)} users…")
    for uid_str in users:
        try:
            await context.bot.send_message(
                int(uid_str),
                f"📢 *Announcement*\n\n{text}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception:
            failed += 1
    await status.edit_text(
        f"✅ Broadcast done!\n✉️ Sent: `{sent}`\n❌ Failed: `{failed}`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

# ── Noop / error ──────────────────────────────────────────────────────────────
async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("Cancelled.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Try again or send /start to reset."
        )

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    app = Application.builder().token(BOT_TOKEN).build()

    # Edit EXIF conversation
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_start, pattern=r"^editstart:")],
        states={
            EDIT_CHOOSING_FIELD: [
                CallbackQueryHandler(edit_field_chosen, pattern=r"^editfield:"),
                CallbackQueryHandler(edit_cancel,       pattern=r"^editcancel"),
            ],
            EDIT_TYPING_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
        per_message=False,
    )

    # Resize conversation (triggered by inline button)
    resize_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_resize_prompt, pattern=r"^resizeprompt:")],
        states={
            RESIZE_TYPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, resize_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
        per_message=False,
    )

    # Poll conversation
    poll_conv = ConversationHandler(
        entry_points=[CommandHandler("poll", poll_cmd)],
        states={
            POLL_TITLE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, poll_title)],
            POLL_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, poll_options)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
        per_message=False,
    )

    # Feedback conversation
    feedback_conv = ConversationHandler(
        entry_points=[CommandHandler("feedback", feedback_cmd)],
        states={
            FEEDBACK_TYPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
        per_message=False,
    )

    # Broadcast conversation (admin)
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_TYPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
        per_message=False,
    )

    # Commands
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_cmd))
    app.add_handler(CommandHandler("history",   history_cmd))
    app.add_handler(CommandHandler("stats",     admin_stats))
    app.add_handler(CommandHandler("users",     admin_users))
    app.add_handler(CommandHandler("ban",       admin_ban))
    app.add_handler(CommandHandler("unban",     admin_unban))
    app.add_handler(CommandHandler("adminhelp", admin_help))
    app.add_handler(CommandHandler("cancel",    cancel_cmd))

    # Conversations
    app.add_handler(edit_conv)
    app.add_handler(resize_conv)
    app.add_handler(poll_conv)
    app.add_handler(feedback_conv)
    app.add_handler(broadcast_conv)

    # Media
    app.add_handler(MessageHandler(filters.PHOTO,          handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(cb_strip,        pattern=r"^strip:"))
    app.add_handler(CallbackQueryHandler(cb_compare,      pattern=r"^compare:"))
    app.add_handler(CallbackQueryHandler(cb_convert_menu, pattern=r"^convertmenu:"))
    app.add_handler(CallbackQueryHandler(cb_do_convert,   pattern=r"^doconvert:"))
    app.add_handler(CallbackQueryHandler(noop,            pattern=r"^noop$"))

    app.add_error_handler(error_handler)

    logger.info("🤖 MetaSnap Bot started. Polling…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,                        # ignore queued msgs on restart
        stop_signals=[signal.SIGINT, signal.SIGTERM],     # graceful shutdown
    )

if __name__ == "__main__":
    main()
