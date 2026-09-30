FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY discordbot ./discordbot
COPY clients ./clients

ENV DISCORD_DATA_DIR=/data PORT=8000 DISCORD_CLIENT=demo-comunitate
VOLUME ["/data"]
EXPOSE 8000

CMD ["sh", "-c", "python -m discordbot --client \"$DISCORD_CLIENT\""]
