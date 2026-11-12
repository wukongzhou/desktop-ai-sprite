FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000
WORKDIR /app

# ffmpeg：AI 动作生成最后一步要靠它从视频抽帧，否则视频生成成功也会失败。
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements-lite.txt ./
RUN pip install --no-cache-dir -r requirements-lite.txt

COPY . .
EXPOSE 10000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}"]
