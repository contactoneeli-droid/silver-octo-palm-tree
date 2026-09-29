FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alerts ./alerts
COPY clients ./clients

ENV ALERTS_DATA_DIR=/data ALERTS_CLIENT=demo-imobiliare
VOLUME ["/data"]

CMD ["sh", "-c", "python -m alerts --client \"$ALERTS_CLIENT\""]
