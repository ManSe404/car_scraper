# Car Watch

Car Watch collects the passenger-car ads from polovniautomobili.com into a SQLite database on your own
machine and helps you judge prices. You get each ad's price history and how long it has been listed, market
statistics per model, and a fair-price model that flags ads listed below what similar cars cost.

It has two parts. A Chrome extension reads the data the site's own pages load (Next.js page data) and keeps
only the car fields. It hands them to Car Watch on your computer through Chrome's
[native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging): Chrome
starts a small Python program, the host, for each message, and the host stores the data in SQLite and plans
further collection. Nothing has to be running beforehand and nothing listens on a network port. A web UI over
the same database runs only when you start it. The UI is in Serbian.

For now the host is installed on Linux only (Google Chrome and Chromium).

```
polovniautomobili.com in your Chrome
   │  the site's own Next.js page data
   ▼
extension/  (WXT, TypeScript, Zod)
   ├─ collector       pages you open: keeps car fields only, drops seller data
   ├─ worker tab      pinned background tab that runs the planned collection work
   ├─ badge           on an ad page: first seen, price changes, days listed
   ├─ popup           status, automatic collection on/off, start a search or a full pass, pause
   └─ background      one native message per request; outbox while the host is missing
   ▼  Chrome starts the host for each message (stdin/stdout, only this extension's id may call it)
service/    (Python: SQLite, pydantic, scikit-learn, FastAPI + Jinja for the UI)
   ├─ carwatch-host       stores ads, price/status sightings and ad details; hands out the next work
   ├─ carwatch revalue    fair-price model, started in its own process when a full pass finishes
   └─ carwatch ui         web UI on 127.0.0.1, only while you run it
```

## How it collects

**Pages you open.** Every search and ad page you open on the site is saved. On an ad page a small box shows
what Car Watch already knows about that ad.

**The worker tab (on by default).** Every 10 minutes the extension asks the host whether there is work.
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

- Car Watch starts no full pass and plans no ad pages by itself;
- an automatic pass that is under way waits, and continues from the same place when you turn it back on;
- what you start yourself still runs: "Pokupi ovu pretragu" collects the search open in the tab, and "Pokreni
  ceo sajt" runs a full pass now (it also takes over a waiting automatic pass).

"Pauziraj 24 h" is different: it stops all collection for a day, your own searches included. A new install
starts with automatic collection on; `CARWATCH_AUTO_FULL_RUNS=false` makes it start off.

## What you get

`uv run carwatch ui` (in `service/`) serves these pages at http://127.0.0.1:8050/ until you stop it:

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
`/runs` or with `uv run carwatch revalue`. The host also sends a desktop notification (`notify-send`) when a
stored ad drops in price.

## Run it

Host (needs [uv](https://docs.astral.sh/uv/) and Python 3.13 or newer):

```sh
cd service
uv sync
uv run carwatch install      # registers the host with Chrome and Chromium
uv run carwatch info         # where the database and the registration live
```

`carwatch install` writes `io.github.manse404.carwatch.json` into `~/.config/google-chrome/NativeMessagingHosts/`
(and the Chromium one, if Chromium has a profile). The file points at `service/.venv/bin/carwatch-host`, so run
`install` again if you move the repo. `uv run carwatch uninstall` removes it.

Extension (needs Node and pnpm):

```sh
cd extension
pnpm install
pnpm build                   # output: extension/.output/chrome-mv3
```

Then in Chrome: `chrome://extensions` → Developer mode → Load unpacked → `extension/.output/chrome-mv3`.
The manifest pins the extension id to `anfomkmnpkpjpjdlpngmmkedbplleiam`, the only id the host accepts. Open any
search or ad on the site; the popup shows whether captures arrive. If it says "Car Watch nije instaliran", run
`uv run carwatch install`.

### Settings

Car Watch reads `CARWATCH_*` environment variables (all optional). The host is started by Chrome, so it sees
Chrome's environment, not your shell's: a variable you export in a terminal reaches `carwatch ui`, but the host
only if Chrome was started with it.

| Variable | Default | Meaning |
|---|---|---|
| `CARWATCH_PORT` | `8050` | Port of the web UI |
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
- **Nothing outside the contract.** The host ignores any field that `service/src/carwatch/models.py` does
  not define.
- **Raw captures expire.** When a collection run finishes, raw page data older than 30 days is emptied. The
  parsed ads and price sightings stay.
- **The data stays on this machine.** The extension talks to the host over stdin/stdout, not over the network,
  and Chrome starts the host only for the extension id named in its registration. The web UI, while it runs,
  listens on 127.0.0.1 only and refuses any other Host header, which blocks DNS-rebinding pages.

The site's terms forbid republishing ad content. Car Watch is for personal use: do not publish or share
the data it collects. Car Watch is not affiliated with polovniautomobili.com.

## Development

```sh
cd service && uv run pytest && uv run ruff check src tests && uv run mypy --strict src
cd extension && pnpm test && pnpm compile && pnpm build
```

The host's tests also start it as a real program with a framed message on stdin, the way Chrome does, and a
test checks that the extension id in `install.py` matches the key in `extension/wxt.config.ts`.

The extension's tests turn the made-up, site-shaped pages in `extension/tests/fixtures/` into the contract
files in `service/tests/fixtures/`, and the Python tests read those same files. A change on either side
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
