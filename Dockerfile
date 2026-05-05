FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libjpeg-dev libpng-dev libtiff-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

# Fix permissions for Koyeb Volumes
RUN chmod 777 /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Create a downloads folder just in case your code expects one
RUN mkdir -p /app/downloads && chmod 777 /app/downloads

CMD ["python", "bot.py"]
