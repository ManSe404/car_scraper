/**
 * The small box on an ad page: what Car Watch already knows about this ad.
 * Built with DOM APIs and textContent only (ad titles come from other people).
 */
import type { CaptureAd } from './extract';
import { daysSince, money, plural, shortDate, signedMoney } from './format';
import type { CaptureResponse } from './messages';

const HOST_ID = 'carwatch-badge-host';

const STYLE = `
  :host { all: initial; }
  .box { position: fixed; right: 16px; bottom: 16px; z-index: 2147483000; width: 300px;
         font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #1d232b;
         background: #fff; border: 1px solid #d9dde3; border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.18);
         padding: 12px 14px; }
  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
  .brand { font-weight: 700; }
  .close { all: unset; cursor: pointer; color: #66707d; font-size: 16px; padding: 0 4px; }
  .price { font-size: 18px; font-weight: 700; }
  .down { color: #13814a; } .up { color: #b4372f; }
  .muted { color: #66707d; }
  .warn { color: #b4372f; }
  .row { margin-top: 4px; }
  a { color: #2f6fdf; text-decoration: none; } a:hover { text-decoration: underline; }
  @media (prefers-color-scheme: dark) {
    .box { background: #1c2026; color: #e6e9ed; border-color: #2c323a; }
    .muted, .close { color: #98a2ae; } .down { color: #5fd394; } .up, .warn { color: #ff8a7f; } a { color: #6ea0ff; }
  }
`;

function el(tag: string, className?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function row(...parts: (string | HTMLElement)[]): HTMLElement {
  const node = el('div', 'row');
  for (const part of parts) node.append(part);
  return node;
}

export function removeBadge(): void {
  document.getElementById(HOST_ID)?.remove();
}

export function showBadge(ad: CaptureAd, response: CaptureResponse | undefined): void {
  removeBadge();
  const host = document.createElement('div');
  host.id = HOST_ID;
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = STYLE;
  const box = el('div', 'box');

  const head = el('div', 'head');
  head.append(el('span', 'brand', 'Car Watch'));
  const close = el('button', 'close', '×');
  close.setAttribute('aria-label', 'Zatvori');
  close.addEventListener('click', () => host.remove());
  head.append(close);
  box.append(head);

  if (!response) {
    box.append(row(el('span', 'warn', 'Ekstenzija ne može da pošalje podatke (osveži stranicu).')));
  } else if (!response.ok) {
    box.append(row(el('span', 'warn', response.error)));
    if (response.queued) box.append(row(el('span', 'muted', 'Snimak čeka i biće poslat kad servis proradi.')));
  } else {
    const summary = response.result.summary;
    if (!summary?.known) {
      box.append(row('Oglas je sačuvan.'));
    } else {
      const price = el('span', 'price', money(summary.price));
      const priceRow = row(price);
      if (summary.first_price !== null && summary.price !== null && summary.first_price !== summary.price) {
        const delta = summary.price - summary.first_price;
        priceRow.append(' ', el('span', delta < 0 ? 'down' : 'up', `${signedMoney(delta)} od prve viđene`));
      }
      box.append(priceRow);

      const firstToday = shortDate(summary.first_seen) === shortDate(new Date().toISOString());
      if (summary.days_seen <= 1 && firstToday) {
        box.append(row('Prvi put viđen danas.'));
      } else {
        box.append(
          row(
            `Prvi put viđen ${shortDate(summary.first_seen)} · viđen ${summary.days_seen} ${plural(summary.days_seen, 'dan', 'dana')}`,
          ),
        );
      }
      if (summary.price_changes > 0) {
        box.append(
          row(
            el(
              'span',
              'muted',
              `Cena menjana ${summary.price_changes}× · najniža ${money(summary.min_price)} · najviša ${money(summary.max_price)}`,
            ),
          ),
        );
      }
      const published = summary.published_at ?? ad.published_at;
      const age = daysSince(published);
      if (published && age !== null) {
        box.append(row(el('span', 'muted', `Na sajtu od ${shortDate(published)} (${age} ${plural(age, 'dan', 'dana')})`)));
      }
      if (ad.damaged && ad.damaged !== 'Nije oštećen') box.append(row(el('span', 'warn', ad.damaged)));
      if (ad.from_abroad) box.append(row(el('span', 'warn', 'Uvoz')));
      if (summary.ui_url) {
        const link = el('a', undefined, 'Istorija u Car Watch ↗') as HTMLAnchorElement;
        link.href = summary.ui_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        box.append(row(link));
      }
    }
  }

  shadow.append(style, box);
  document.body.append(host);
}

const BANNER_ID = 'carwatch-worker-banner';

/** A strip at the top of the worker tab, so it is obvious what this tab is doing. */
export function workerBanner(): { update: (text: string) => void } {
  document.getElementById(BANNER_ID)?.remove();
  const host = document.createElement('div');
  host.id = BANNER_ID;
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = `
    .bar { position: fixed; top: 0; left: 0; right: 0; z-index: 2147483000; padding: 10px 16px;
           font: 14px/1.4 system-ui, sans-serif; color: #fff; background: #2f6fdf; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
    .small { opacity: .85; font-size: 12px; }
  `;
  const bar = el('div', 'bar');
  const text = el('div', undefined, 'Car Watch povlači podatke…');
  const hint = el('div', 'small', 'Ovaj tab je otvorila Car Watch ekstenzija i zatvoriće ga sama. Zatvori ga ako želiš da prekineš.');
  bar.append(text, hint);
  shadow.append(style, bar);
  (document.body ?? document.documentElement).append(host);
  return { update: (value: string) => void (text.textContent = value) };
}
