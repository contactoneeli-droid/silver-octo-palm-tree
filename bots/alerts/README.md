# Bot de alerte pentru anunțuri noi

Trimite pe Telegram anunțurile noi de pe **OLX**, **Storia** și **Autovit** la câteva minute
după ce apar. Utilizatorul face căutarea pe site cu filtrele lui (zonă, preț, camere, marcă...),
copiază linkul și i-l trimite botului. Botul verifică pagina periodic, ține minte ce a văzut și
trimite doar ce e nou, cu titlu, preț, localitate și link (Telegram arată și poza).

Se vinde ca abonament lunar per utilizator: proprietarul activează fiecare utilizator cu
`/aproba ID [ZILE]`, iar la expirare alertele se opresc singure și amândoi sunt anunțați.

## Ce face

- Utilizator: trimite un link (sau `/adauga LINK nume`), `/lista`, `/sterge N`, `/pauza N`,
  `/pornire N`, `/verifica N`, `/status`. Când adaugă o căutare vede imediat cele mai noi
  `initial_results` anunțuri, ca să știe că merge.
- Verificare la fiecare `poll_minutes` per căutare; cel mult `max_per_check` anunțuri noi pe
  verificare (peste 3 vin grupate într-un singur mesaj). Anunțurile văzute se țin minte 120 de zile.
- Acces: `open` (oricine) sau `approved` (proprietarul activează, opțional cu `trial_days` de probă).
  `/aproba ID` prelungește de la expirarea curentă. La expirare: mesaj către utilizator și proprietar.
- Erori: dacă o pagină nu se poate citi de 3 ori la rând, proprietarul primește un mesaj și
  căutarea e verificată mai rar până își revine; la succes revine la normal.
- Proprietar: `/utilizatori`, `/aproba ID [ZILE]`, `/blocheaza ID`, `/stats`, `/ruleaza`.

## Cum citește site-urile

Fără API-uri oficiale: botul descarcă pagina de rezultate sortată după „cele mai noi” și
citește JSON-ul pe care site-urile îl pun în pagină (`__NEXT_DATA__` la Storia și Autovit,
`__PRERENDERED_STATE__` la OLX). Câmpurile sunt căutate flexibil (id, titlu, link, preț,
localitate, poză), iar dacă JSON-ul lipsește cade pe linkurile de anunțuri din HTML.

**De verificat la prima pornire pe un server real**: structura paginilor a fost scrisă după
formatul cunoscut al celor trei site-uri, dar nu a putut fi testată pe paginile live din mediul
în care a fost dezvoltat botul. Dacă `/verifica` spune „nu am găsit anunțuri în pagină”, salvează
pagina și ajustează `sources.py`. Testele folosesc pagini construite după același format.

Site-urile pot bloca IP-urile de datacenter (HTTP 403). Atunci setezi `HTTPS_PROXY` către un
proxy rezidențial; `httpx` îl folosește automat. Botul face cel mult o cerere la 2 secunde per site.

## Configurare client

`clients/<slug>/config.yaml`: nume, `access`, `trial_days`, `subscription_days`,
`max_searches_per_user`, `poll_minutes`, `initial_results`, `max_per_check`, `sources`, texte.
Vezi `clients/demo-imobiliare/config.yaml`.

## Pornire

```bash
cd bots/alerts
pip install -r requirements-dev.txt
cp .env.example .env   # completează, apoi: export $(grep -v '^#' .env | xargs)
python -m alerts --client demo-imobiliare
```

`python -m alerts --client demo-imobiliare --once` face o singură trecere prin toate căutările
și iese (bun pentru cron, fără proces permanent). Datele stau în `data/<slug>.sqlite3`
(`ALERTS_DATA_DIR`).

## Teste

```bash
pytest
```

## Limite cunoscute

- Doar OLX, Storia, Autovit. Alte site-uri (imobiliare.ro, publi24, eMAG) se adaugă în
  `sources.py` cu un nume de gazdă, parametrul de sortare și, la nevoie, un parser.
- Plata abonamentului e manuală (proprietarul activează). Pentru plată cu cardul se poate
  combina cu botul de grupuri (`bots/members`), care are deja Stripe.
- Fără filtre suplimentare peste cele ale site-ului (ex. „exclude agenții”).
