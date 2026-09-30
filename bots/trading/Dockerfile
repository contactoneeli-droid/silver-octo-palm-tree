FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY trading ./trading
COPY clients ./clients

ENV TRADING_DATA_DIR=/data TRADING_CLIENT=demo-crypto TRADING_MODE=paper
VOLUME ["/data"]

CMD ["sh", "-c", "python -m trading \"$TRADING_MODE\" --client \"$TRADING_CLIENT\""]
