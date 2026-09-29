# Bot pentru grupuri Telegram plătite

Vinde acces la un grup sau canal privat de Telegram: clientul alege un abonament,
plătește cu cardul (Stripe) sau prin transfer, primește automat un link de acces de
unică folosință, e anunțat înainte să-i expire abonamentul și e scos din grup dacă nu
reînnoiește. Proprietarul vede totul pe Telegram.

## Ce face

- `/start` sau `/planuri`: lista abonamentelor cu preț și durată, buton de probă
  gratuită (o singură dată per utilizator) și buton pentru cod de reducere.
- Plata cu cardul: Stripe Checkout, în moneda din config (RON, EUR...). După plată,
  webhook-ul Stripe activează accesul și botul trimite linkul de invitație (valabil
  24 h, un singur membru). Reîncercările Stripe nu dublează accesul.
- Plata manuală (`payment.provider: manual`): botul afișează instrucțiunile de transfer,
  clientul apasă „Am plătit”, proprietarul primește `/aproba ID PLAN` și activează.
- Reînnoirea prelungește de la data expirării curente, nu de azi.
- Zilnic (la fiecare 10 minute, de fapt): reminder cu `remind_days_before` zile înainte
  de expirare, apoi scoatere din grup după expirare + `grace_days`, cu mesaj și buton de
  reînnoire. Scoaterea e „kick”, nu ban: după plată poate reveni.
- Proprietar: `/stats`, `/membri`, `/aproba ID PLAN`, `/prelungeste ID ZILE`,
  `/scoate ID`, `/link ID`, `/help`.

## Configurare client

`clients/<slug>/config.yaml`: numele, `group_chat_id` (grupul privat; botul trebuie să
fie administrator cu drept de invitare și de eliminare a membrilor), `plans`, `trial_days`,
`grace_days`, `remind_days_before`, `coupons`, `payment.provider` și textele. Vezi
`clients/demo-creator/config.yaml`.

Cum afli `group_chat_id`: adaugi botul în grup ca admin și trimiți un mesaj; ID-ul apare
în log la `getUpdates`, sau folosești @userinfobot / @getidsbot.

## Pornire

```bash
cd bots/members
pip install -r requirements-dev.txt
cp .env.example .env   # completează, apoi: export $(grep -v '^#' .env | xargs)
python -m members --client demo-creator
```

Stripe: creezi un webhook în Dashboard către `https://<PUBLIC_URL>/stripe/webhook` pentru
evenimentul `checkout.session.completed` și pui secretul în `STRIPE_WEBHOOK_SECRET`.
Fără chei Stripe botul pornește automat pe plăți manuale.

`--no-web` rulează doar partea de Telegram (util pentru plăți manuale, fără server HTTP).

## Teste

```bash
pytest
```

Testele nu au nevoie de Telegram sau Stripe reale; semnătura webhook-ului e verificată
cu algoritmul real al Stripe.

## Limite cunoscute

- Plățile sunt per perioadă (nu abonament Stripe cu reînnoire automată a cardului);
  clientul primește reminder și plătește din nou. Reînnoirea automată e un pas următor.
- Plata în crypto nu e încă implementată (interfața de plată e simplă: un URL de checkout
  și o confirmare cu referință unică).
