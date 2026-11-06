# Car Watch

Car Watch collects the passenger-car ads from polovniautomobili.com into a SQLite database on your own
machine and helps you judge prices. You get each ad's price history and how long it has been listed, market
statistics per model, and a fair-price model that flags ads listed below what similar cars cost.

It has two parts. A Chrome extension reads the data the site's own pages load (Next.js page data), keeps
only the car fields and sends them to a small local service. The service stores them, plans further
collection and serves a web UI. The UI is in Serbian.

```
polovniautomobili.com in your Chrome
   │  the site's own Next.js page data
   ▼
extension/  (WXT, TypeScript, Zod)
   ├─ collector       pages you open: keeps car fields only, drops seller data
   ├─ worker tab      pinned background tab that runs the service's collection work
   ├─ badge           on an ad page: first seen, price changes, days listed
   ├─ popup           status, automatic collection on/off, start a search or a full pass, pause
   └─ background      POST to 127.0.0.1:8050 with a token; outbox while the service is down
   ▼
service/    (FastAPI, SQLite, Jinja, scikit-learn)
   ├─ /api/captures   stores ads, price/status sightings and ad details
   ├─ /api/work       hands the extension what to fetch next
   └─ web UI          ads, market, deals, compare, searches, collection runs
```

## How it collects

**Pages you open.** Every search and ad page you open on the site is saved. On an ad page a small box shows
what Car Watch already knows about that ad.

**The worker tab (on by default).** Every 10 minutes the extension asks the service whether there is work.
If there is, it opens a pinned background tab on the site and fetches the planned pages from there, inside
your own browser session:

- **A full pass over the whole site every 7 days.** Search results are split into price bands because the site
  stops paging deep result lists. One pass is about 3,000 search pages.
- **Ad pages for details** (damage, registration, origin, equipment), up to 600 a day, best deals first.
- **One request every 3 to 4 seconds**, a fixed gap plus up to a second of random jitter.
- **When the site refuses a request** (403, 429, 503 or a challenge page), all collection stops for 12 hours.
  Nothing tries to get around the site's bot protection.

**Turning it off.** In the popup, "Isključi automatsko" turns automatic collection off and "Uključi automatsko"
turns it back on. While it is off:

- the service starts no full pass and plans no ad pages by itself;
- an automatic pass that is under way waits, and continues from the same place when you turn it back on;
- what you start yourself still runs: "Pokupi ovu pretragu" collects the search open in the tab, and "Pokreni
  ceo sajt" runs a full pass now (it also takes over a waiting automatic pass).

"Pauziraj 24 h" is different: it stops all collection for a day, your own searches included. A new install
starts with automatic collection on; `CARWATCH_AUTO_FULL_RUNS=false` makes it start off.

## What you get

| Page | What it shows |
|---|---|
| Oglasi `/` | Every stored ad, with filters: price drop, price, year, mileage… |
| `/ads/{id}` | One ad: price chart, details, every sighting |
| Tržište `/market` | Per brand and model: median price, year, mileage, power, yearly loss of value |
| Ponude `/deals` | Ads listed below the model's expected price |
| Uporedi `/compare` | Median price by year, and what a budget buys, for the models you pick |
| Pretrage `/searches` | The searches you ran, with how many ads each one returned |
| Povlačenje `/runs` | Collection progress; start, pause, resume or cancel a run |

The fair-price model is gradient boosting on log price. Its predictions are out-of-fold: every ad's expected
price comes from a model trained without that ad. It is retrained when a full pass finishes, or on demand from
`/runs`. On Linux the service also sends a desktop notification (`notify-send`) when a stored ad drops in price.

## Run it

Service (needs [uv](https://docs.astral.sh/uv/) and Python 3.13 or newer):

```sh
cd service
uv sync
uv run carwatch serve        # http://127.0.0.1:8050/ (creates ~/.config/carwatch/token on first run)
uv run carwatch info         # where the database and token live
```

Extension (needs Node and pnpm). Build it after the service has created the token, because the build bakes
the token in:

```sh
cd extension
pnpm install
pnpm build                   # output: extension/.output/chrome-mv3
```

Then in Chrome: `chrome://extensions` → Developer mode → Load unpacked → `extension/.output/chrome-mv3`.
Open any search or ad on the site; the popup shows whether captures arrive.

To keep the service running, `service/deploy/carwatch.service` is a systemd user unit. It assumes the repo
lives at `~/Projekti/Personal/car_scraper`; change the two paths if yours is elsewhere.

### Settings

The service reads `CARWATCH_*` environment variables (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `CARWATCH_PORT` | `8050` | Service port. Set the same value when you build the extension. |
| `CARWATCH_AUTO_FULL_RUNS` | `true` | Starting position of the popup's automatic switch; once you use the switch, its choice wins |
| `CARWATCH_CRAWL_INTERVAL_DAYS` | `7` | Days between full passes |
| `CARWATCH_DETAILS_DAILY_LIMIT` | `600` | Ad pages fetched per day for details |
| `CARWATCH_REQUEST_GAP_SECONDS` | `3.0` | Gap between two requests to the site, before the jitter |
| `CARWATCH_BLOCKED_PAUSE_HOURS` | `12` | How long collection waits after the site refuses |
| `CARWATCH_NOTIFY` | `true` | Desktop notifications on price drops |
| `CARWATCH_DB_PATH` | `~/.local/share/carwatch/carwatch.db` | Database file |

The full list, with comments, is in `service/src/carwatch/config.py`.

## What is stored, and what is not

- **Only car fields.** An allow-list in `extension/lib/extract.ts` picks them. Seller names, seller and dealer
  ids, contact details and profile photos never leave the browser.
- **No contacts in descriptions.** Phone numbers and e-mail addresses are removed from ad descriptions.
- **Nothing outside the contract.** The service ignores any field that `service/src/carwatch/models.py` does
  not define.
- **Raw captures expire.** When a collection run finishes, raw page data older than 30 days is emptied. The
  parsed ads and price sightings stay.
- **The data stays on this machine.** The service listens on 127.0.0.1 only, every API call needs the token,
  and requests with any other Host header are refused, which blocks DNS-rebinding pages.

The site's terms forbid republishing ad content. Car Watch is for personal use: do not publish or share
the data it collects. Car Watch is not affiliated with polovniautomobili.com.

## Development

```sh
cd service && uv run pytest && uv run ruff check src tests && uv run mypy --strict src
cd extension && pnpm test && pnpm compile && pnpm build
```

The extension's tests turn the made-up, site-shaped pages in `extension/tests/fixtures/` into the contract
files in `service/tests/fixtures/`, and the service's tests read those same files. A change on either side
of the contract fails a test. The site-shaped pages deliberately contain a seller name, phone and address,
so the tests also prove those are dropped. If the site changes its data, the popup shows "Sajt je promenio
format" and nothing is stored.

## History

The original 2023 requests + BeautifulSoup scraper was removed. It stopped working when the site moved to
Next.js behind a Cloudflare challenge. It is still in git history: `git show 2546d05:main.py`.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md). The code is free for learning, research, and personal and hobby
projects, and for educational and research institutions, charities and government bodies. Commercial use
needs a separate agreement with the author.
