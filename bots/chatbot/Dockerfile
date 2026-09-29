FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chatbot ./chatbot
COPY widget ./widget
COPY clients ./clients

ENV CHATBOT_DATA_DIR=/data PORT=8000 CHATBOT_CLIENT=demo-salon
VOLUME ["/data"]
EXPOSE 8000

# "all" runs the web API + widget and, when TELEGRAM_BOT_TOKEN is set, the Telegram bot.
CMD ["sh", "-c", "python -m chatbot --client \"$CHATBOT_CLIENT\" $( [ -n \"$TELEGRAM_BOT_TOKEN\" ] && echo all || echo web )"]
