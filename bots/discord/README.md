# Șablon de bot Discord

Un singur bot, configurat per client dintr-un fișier YAML, care acoperă ce cer de obicei
comunitățile: mesaj de bun venit cu rol automat, moderare (cuvinte interzise, anti-spam,
linkuri de invitație, avertismente cu pauză automată), tichete de suport în canale private,
roluri plătite prin Stripe, niveluri cu XP și trivia. Fiecare modul se poate opri din config.

## Module

- **Bun venit**: mesaj în canalul ales (`{mention}`, `{name}`, `{server}`, `{count}`, `{rules}`),
  rol automat la intrare, mesaj privat opțional.
- **Moderare**: șterge mesajele cu cuvinte interzise (fără diacritice, pe cuvinte întregi),
  linkurile `discord.gg`, și spam-ul (mai mult de N mesaje în M secunde). Fiecare abatere dă un
  avertisment; la `warn_limit` membrul primește pauză (timeout) de `timeout_minutes` și
  avertismentele se resetează. Totul se scrie în `log_channel`. Rolurile din `exempt_roles` și
  cine are „Manage Messages” sunt exceptate. Comenzi: `/avertizeaza`, `/avertismente`, `/iarta`,
  `/curata`.
- **Tichete**: `/panou_tichete` postează un buton; la apăsare (sau `/tichet`) se creează un canal
  privat în categoria `Tichete`, vizibil doar membrului, echipei (`support_role`) și botului.
  `/inchide` sau butonul închid tichetul și șterg canalul după 30 de secunde. Un tichet deschis
  per membru (configurabil).
- **Roluri plătite**: `/abonamente` arată planurile cu buton de plată Stripe; după plată webhook-ul
  dă rolul și trimite un mesaj privat. Reînnoirea prelungește de la expirarea curentă. La fiecare
  10 minute botul scoate rolurile expirate și trimite reminder cu `remind_days_before` zile înainte.
- **Niveluri**: 15-25 XP per mesaj (cel mult o dată pe minut), nivel = √(XP/100), roluri de nivel
  (`level_roles`), `/nivel`, `/top`. **Trivia**: `/trivia` pune o întrebare din
  `clients/<slug>/trivia.yaml` cu butoane; primul răspuns corect ia XP.
- `/setari` (admin) arată ce module rulează și cu ce valori.

## Configurare client

`clients/<slug>/config.yaml` + opțional `trivia.yaml`. Vezi `clients/demo-comunitate/`.
Canalele și rolurile se dau după nume, exact cum sunt pe serverul clientului; botul nu le creează
(cu excepția categoriei de tichete și a canalelor de tichet).

## Instalare pe serverul clientului

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application → Bot:
   copiază token-ul; activează **Server Members Intent** și **Message Content Intent**.
2. OAuth2 → URL Generator: scope `bot` + `applications.commands`; permisiuni: Manage Roles,
   Manage Channels, Kick/Timeout Members (Moderate Members), Manage Messages, Read/Send Messages,
   Read Message History. Deschide URL-ul și adaugă botul pe server.
3. Rolul botului trebuie să fie **deasupra** rolurilor pe care le dă (Membru, VIP, Veteran...).
4. `guild_id` în config (Setări server → Widget, sau click dreapta pe server cu modul dezvoltator).

```bash
cd bots/discord
pip install -r requirements-dev.txt
cp .env.example .env   # completează, apoi: export $(grep -v '^#' .env | xargs)
python -m discordbot --client demo-comunitate
```

Comenzile slash apar pe server la prima pornire (sincronizare pe serverul din config, instantă).
Pentru roluri plătite: webhook Stripe către `https://<PUBLIC_URL>/stripe/webhook` pe evenimentul
`checkout.session.completed`, cu secretul în `STRIPE_WEBHOOK_SECRET`.

## Teste

```bash
pytest
```

Testele acoperă regulile (filtru, spam, escaladare, niveluri, roluri plătite), configurația,
înregistrarea comenzilor și webhook-ul Stripe. Partea care vorbește cu Discord (evenimente,
crearea canalelor) nu are teste automate: se verifică pe un server de test la prima instalare.

## Limite cunoscute

- Un bot rulează pentru un singur server (un proces per client, ca la ceilalți boți).
- Jocuri: doar trivia și niveluri. Alte jocuri (ghicește numărul, ruletă de XP) se adaugă ca
  o comandă nouă în `bot.py`.
- Fără panou web de setări: configurarea e fișierul YAML (pe pagina de vânzare e promis „panou
  simplu de setări”; `/setari` arată valorile, dar schimbarea lor cere editarea fișierului și
  repornirea botului).
