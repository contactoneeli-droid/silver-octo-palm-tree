# Chatbot AI pentru afaceri mici (+ programări)

Un singur cod, un director de configurare per client. Botul răspunde clienților
din informațiile afacerii (prețuri, program, servicii), preia cereri de
programare sau ofertă și le trimite proprietarului pe Telegram, iar când nu știe
sau clientul cere un om, anunță un coleg.

Cu **modulul de programări** activat (`booking.enabled: true`), același bot devine
botul de programări: știe programul și serviciile, verifică orele libere, face
programarea pe loc, anunță proprietarul și trimite clientului un reminder pe
Telegram înainte de programare.

Canale: widget pe site (orice site, un singur `<script>`), Telegram. WhatsApp
urmează, prin WhatsApp Business API.

## Structură

```
chatbot/         codul: engine (Claude), booking (calendar), reminders, telegram, web
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
Programările prin chat au nevoie de motorul real (cheie API).

Telegram: creezi botul la @BotFather, pui token-ul în `TELEGRAM_BOT_TOKEN` și rulezi
`python -m chatbot --client demo-salon telegram` (sau `all` pentru ambele).

## Client nou

1. `cp -r clients/demo-salon clients/<slug>`
2. Editezi `config.yaml` (nume, ton, salut, ce domenii au voie să încarce widget-ul).
3. Scrii în `knowledge/*.md` tot ce trebuie să știe botul: prețuri, program, servicii,
   întrebări frecvente. Text simplu, în limba clientului.
4. Programări: în secțiunea `booking` pui programul pe zile, serviciile cu durata,
   câte programări pot fi în paralel (`capacity`) și dacă se confirmă automat.
   Pentru un client care vrea doar întrebări și răspunsuri, `enabled: false`.
5. `OWNER_TELEGRAM_CHAT_ID` = chat-ul unde vrea proprietarul să primească cererile.
6. Pornești cu `--client <slug>`.

Widget-ul pe site-ul clientului:

```html
<script src="https://<adresa-botului>/widget.js" async></script>
```

## Cum răspunde

- Model: `claude-opus-5-5` (configurabil per client în `config.yaml`), efort `low`,
  potrivit pentru conversații scurte.
- Knowledge-ul e trimis ca system prompt cu prompt caching, deci costul pe mesaj e mic.
- Fiecare mesaj primește data și ora curentă, ca „mâine” sau „sâmbăta asta” să însemne
  ceva; istoricul păstrat e doar textul.
- Instrumente: `save_request`, `escalate_to_human` și, cu programări active,
  `get_free_slots`, `book_appointment`, `cancel_appointment`. Botul nu poate confirma o
  oră decât prin rezultatul instrumentului, deci nu inventează disponibilitate.
- Istoricul conversației e păstrat per canal + chat (ultimele 20 de mesaje).
- Refuzurile de siguranță și erorile de API primesc mesajul `fallback_message`.

## Programări

- Orele libere se calculează din program, durata serviciului, pasul `slot_minutes`,
  `capacity` și programările existente (SQLite, ore stocate în UTC).
- `auto_confirm: true` confirmă pe loc; `false` lasă programarea „în așteptare” până
  proprietarul dă `/confirma N` pe Telegram.
- Proprietarul (chat-ul din `OWNER_TELEGRAM_CHAT_ID`) are comenzile `/azi`, `/maine`,
  `/programari AAAA-LL-ZZ`, `/confirma N`, `/anuleaza N`.
- Remindere: cu `remind_hours_before` ore înainte, clienții care au scris pe Telegram
  primesc un mesaj. Clienții din widget-ul web nu pot fi contactați înapoi (nu au canal);
  reminderele pe WhatsApp sau SMS vin odată cu acele canale.
- Sincronizarea cu Google Calendar nu e încă făcută; calendarul e al botului.

## Teste

```bash
pytest
```

## Deploy

`Dockerfile` inclus. Variabilele din `.env.example`, volum pe `/data` pentru baza
SQLite. Merge pe Railway, Fly.io sau orice VPS cu Docker.
