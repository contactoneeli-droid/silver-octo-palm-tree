FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY members ./members
COPY clients ./clients

ENV MEMBERS_DATA_DIR=/data PORT=8000 MEMBERS_CLIENT=demo-creator
VOLUME ["/data"]
EXPOSE 8000

CMD ["sh", "-c", "python -m members --client \"$MEMBERS_CLIENT\""]
