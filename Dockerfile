FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev libpng-dev libtiff-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# data.json is written at runtime — attach a Koyeb persistent volume
# to /app so user data survives redeploys.

CMD ["python", "bot.py"]
