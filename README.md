# 📸 MetaSnap Bot — Full Edition

A feature-rich Telegram bot for extracting, editing, and stripping photo metadata.

---

## ✨ What's New vs Basic Version

| Feature | Basic | Full |
|---|:---:|:---:|
| Metadata extraction | ✅ | ✅ |
| Strip metadata | ✅ | ✅ |
| ✏️ Edit EXIF fields | ❌ | ✅ |
| 📊 Before/after compare | ❌ | ✅ |
| 📐 Resize image | ❌ | ✅ |
| 🔄 Convert format (JPEG/PNG/WEBP) | ❌ | ✅ |
| 📋 Scan history (/history) | ❌ | ✅ |
| 🗳 Create polls (/poll) | ❌ | ✅ |
| 💬 User feedback (/feedback) | ❌ | ✅ |
| 🆕 New-user admin alert | ❌ | ✅ |
| 🚫 Ban / Unban users | ❌ | ✅ |
| 📢 Admin broadcast | ❌ | ✅ |
| 📊 Admin stats | ❌ | ✅ |
| 👥 Admin user list | ❌ | ✅ |
| 💾 Persistent JSON datastore | ❌ | ✅ |
| 🛑 Graceful shutdown (SIGINT/SIGTERM) | ❌ | ✅ |
| ⚠️ Global error handler | ❌ | ✅ |

---

## 🤖 All Commands

### User Commands
| Command | Description |
|---|---|
| `/start` | Welcome message and feature overview |
| `/help` | Full usage guide |
| `/history` | Your last 5 image scans |
| `/poll` | Step-by-step poll creator |
| `/feedback` | Send a message to the admin |
| `/cancel` | Exit any active operation |

### Admin Commands
| Command | Description |
|---|---|
| `/stats` | Total scans, strips, edits, user counts |
| `/users` | List all users with activity counts |
| `/ban <uid>` | Ban a user (they get notified) |
| `/unban <uid>` | Unban a user (they get notified) |
| `/broadcast` | Send announcement to all users |
| `/adminhelp` | Admin command reference |

### Inline Action Buttons (appear after every scan)
| Button | Description |
|---|---|
| 🗑 Strip Metadata | Remove all EXIF, get clean image |
| ✏️ Edit EXIF | Change Author, Copyright, DateTime, etc. |
| 📊 Compare Size | Side-by-side size & field count report |
| 📐 Resize | Enter custom dimensions (e.g. `800 600`) |
| 🔄 Convert Format | Switch between JPEG, PNG, WEBP |

---

## 🚀 Local Setup

```bash
git clone <your-repo>
cd tg-meta-bot

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt

export BOT_TOKEN="your_token_here"
export ADMIN_IDS="123456789"          # your Telegram user ID (find via @userinfobot)
# Multiple admins: ADMIN_IDS="111,222,333"

python bot.py
```

---

## ☁️ Deploy to Koyeb

### Step 1 — Push to GitHub
```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOU/metasnap-bot.git
git push -u origin main
```

### Step 2 — Create Service on Koyeb
1. Go to [koyeb.com](https://app.koyeb.com) → **Create Service**
2. Source: **GitHub** → select your repo
3. Builder: **Dockerfile** (auto-detected)
4. Instance: **Eco** (free tier is fine)
5. Health checks: **Disable** (bots don't serve HTTP)

### Step 3 — Environment Variables
| Key | Value |
|---|---|
| `BOT_TOKEN` | Token from @BotFather |
| `ADMIN_IDS` | Your Telegram numeric user ID |

### Step 4 — Persistent Storage (important!)
`data.json` stores all user data. Without a persistent volume it resets on each deploy.

In Koyeb: **Service → Storage → Add Volume → Mount path `/app`**

Then click **Deploy**.

---

## 📁 Project Structure

```
tg-meta-bot/
├── bot.py            ← entire bot (single file)
├── requirements.txt  ← Python dependencies
├── Dockerfile        ← production Docker image
├── Procfile          ← Koyeb buildpack fallback
└── README.md         ← this file
```

---

## 🔑 Getting Your Telegram User ID
Send any message to [@userinfobot](https://t.me/userinfobot) — it replies with your numeric ID.

## 🤖 Creating a Bot Token
1. Open Telegram → search **@BotFather**
2. `/newbot` → follow prompts
3. Copy the token

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `python-telegram-bot` | Telegram Bot API wrapper |
| `Pillow` | Image processing (resize, convert, strip) |
| `piexif` | Read & write EXIF metadata |
