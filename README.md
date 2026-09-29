# botzi

Boți de vânzare pentru afaceri mici: WhatsApp, Telegram și Discord.

## Structură

- `site/` — pagina de prezentare și vânzare (HTML static, publicată pe GitHub Pages)
- `bots/` — câte un director pentru fiecare bot:
  - `bots/chatbot/` — chatbot AI pentru site și Telegram, cu modul de programări
  - `bots/members/` — bot pentru grupuri Telegram plătite (abonamente, Stripe, acces automat)

## Pagina

Pagina se publică automat pe GitHub Pages la fiecare push pe `main` care atinge `site/`.
Local: deschide `site/index.html` în browser.
