# botzi

Boți de vânzare pentru afaceri mici: WhatsApp, Telegram și Discord.

## Structură

- `site/` — pagina de prezentare și vânzare (HTML static, publicată pe GitHub Pages)
- `bots/` — câte un director pentru fiecare bot:
  - `bots/chatbot/` — chatbot AI pentru site și Telegram, cu modul de programări
  - `bots/members/` — bot pentru grupuri Telegram plătite (abonamente, Stripe, acces automat)
  - `bots/alerts/` — alerte pe Telegram pentru anunțuri noi de pe OLX, Storia și Autovit
  - `bots/discord/` — șablon de bot Discord (bun venit, moderare, tichete, roluri plătite, niveluri, trivia)
  - `bots/trading/` — bot de trading (backtest, simulare, live prin ccxt, rapoarte pe Telegram)

## Pagina

Pagina se publică automat pe GitHub Pages la fiecare push pe `main` care atinge `site/`.
Local: deschide `site/index.html` în browser.
