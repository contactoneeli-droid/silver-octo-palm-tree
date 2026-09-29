# Chatbot AI pentru afaceri mici

Un singur cod, un director de configurare per client. Botul răspunde clienților
din informațiile afacerii (prețuri, program, servicii), preia cereri de
programare sau ofertă și le trimite proprietarului pe Telegram, iar când nu știe
sau clientul cere un om, anunță un coleg.

Canale: widget pe site (orice site, un singur `<script>`), Telegram. WhatsApp
urmează, prin WhatsApp Business API.

## Structură

```
chatbot/         codul (motor Claude, Telegram, API web)
widget/          widget.js, scriptul care se pune pe site
clients/<slug>/  config.yaml + knowledge/*.md pentru fiecare client
tests/           teste (pytest), rulează fără cheie API
```

## Pornire locală

```bash
cd bots/chatbot
pip install -r requirements-dev.txt
cp .env.example .env            # completează cheile, apoi: export $(grep -v '^#' .env | xargs)
python -m chatbot --client demo-salon web
```

Deschide http://localhost:8000 pentru pagina de test cu widget. Fără
`ANTHROPIC_API_KEY` botul rulează în **mod demo** (răspunde prin potrivire de
cuvinte din knowledge, marcat ca atare), util ca să vezi fluxul fără costuri.

Telegram: creezi botul la @BotFather, pui token-ul în `TELEGRAM_BOT_TOKEN` și rulezi
`python -m chatbot --client demo-salon telegram` (sau `all` pentru ambele).

## Client nou

1. `cp -r clients/demo-salon clients/<slug>`
2. Editezi `config.yaml` (nume, ton, salut, ce domenii au voie să încarce widget-ul).
3. Scrii în `knowledge/*.md` tot ce trebuie să știe botul: prețuri, program, servicii,
   întrebări frecvente, cum se fac programările. Text simplu, în limba clientului.
4. `OWNER_TELEGRAM_CHAT_ID` = chat-ul unde vrea proprietarul să primească cererile.
5. Pornești cu `--client <slug>`.

Widget-ul pe site-ul clientului:

```html
<script src="https://<adresa-botului>/widget.js" async></script>
```

## Cum răspunde

- Model: `claude-opus-5-5` (configurabil per client în `config.yaml`), efort `low`,
  potrivit pentru conversații scurte.
- Knowledge-ul e trimis ca system prompt cu prompt caching, deci costul pe mesaj e mic.
- Două instrumente: `save_request` (salvează cererea în SQLite și anunță proprietarul)
  și `escalate_to_human` (anunță proprietarul că trebuie să preia).
- Istoricul conversației e păstrat per canal + chat (ultimele 20 de mesaje).
- Refuzurile de siguranță și erorile de API primesc mesajul `fallback_message`.

## Teste

```bash
pytest
```

## Deploy

`Dockerfile` inclus. Variabilele din `.env.example`, volum pe `/data` pentru baza
SQLite. Merge pe Railway, Fly.io sau orice VPS cu Docker.
