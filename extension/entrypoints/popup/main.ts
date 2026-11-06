import { browser } from 'wxt/browser';
import { shortTime } from '../../lib/format';
import type { Message, Result, StatusResponse } from '../../lib/messages';

const app = document.getElementById('app')!;

function el(tag: string, text?: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(label: string, onClick: () => void, secondary = false): HTMLElement {
  const node = el('button', label, secondary ? 'secondary' : undefined);
  node.addEventListener('click', onClick);
  return node;
}

async function ask<T>(message: Message): Promise<T> {
  return (await browser.runtime.sendMessage(message)) as T;
}

/** The search open in the current tab, if it is one (activeTab is granted when the popup opens). */
async function currentSearchUrl(): Promise<string | null> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url ?? '';
  return url.startsWith('https://www.polovniautomobili.com/auto-oglasi/pretraga') ? url : null;
}

function serviceLine(s: StatusResponse): HTMLElement {
  const line = el('div', undefined, 'status');
  const dot = el('span', undefined, `dot ${s.service.ok ? 'ok' : 'bad'}`);
  const text = el('div');
  if (s.service.ok) {
    text.append(el('div', `Car Watch radi · ${s.service.ads.toLocaleString('sr-RS')} oglasa u bazi`));
  } else if (s.service.kind === 'offline') {
    text.append(el('div', s.service.error));
    if (s.service.error.includes('nije instaliran')) {
      const hint = el('div', undefined, 'muted');
      hint.append('U ', el('code', 'service/'), ' pokreni ', el('code', 'uv run carwatch install'));
      text.append(hint);
    }
  } else {
    text.append(el('div', s.service.error, 'error'));
  }
  line.append(dot, text);
  return line;
}

function render(s: StatusResponse, searchUrl: string | null): void {
  app.replaceChildren();
  app.append(el('h1', 'Car Watch'), serviceLine(s));

  const facts = el('dl');
  const add = (label: string, value: string): void => {
    facts.append(el('dt', label), el('dd', value));
  };
  const work = s.work;
  if (work) {
    if (work.paused_until) add('Povlačenje', `pauzirano do ${shortTime(work.paused_until)}`);
    else if (s.workerActive) add('Povlačenje', 'radi u pozadinskom tabu');
    else add('Povlačenje', work.has_work ? 'čeka sledeću proveru (do 10 min)' : 'nema posla');
    add('Automatsko', work.auto ? 'uključeno' : 'isključeno');
    if (work.run) {
      const r = work.run;
      const what = r.kind === 'full' ? 'Ceo sajt' : 'Pretraga';
      const waiting = r.status === 'running' && r.auto && !work.auto;
      const state = waiting ? 'čeka' : r.status === 'running' ? 'u toku' : r.status === 'done' ? 'završeno' : r.status;
      add(what, `${state} · ${r.tasks_done.toLocaleString('sr-RS')} / ${r.tasks_total.toLocaleString('sr-RS')} strana`);
    }
    if (work.auto) {
      add('Sledeći ceo sajt', work.next_full_run ? shortTime(work.next_full_run) : 'odmah');
      add('Detalji danas', `${work.details_today} / ${work.details_limit}`);
    }
  }
  add('Snimljeno danas', `${s.stats.today.captures} stranica`);
  if (s.queued > 0) add('Čeka slanje', `${s.queued}`);
  app.append(facts);

  if (s.stats.lastError) {
    app.append(el('div', `${shortTime(s.stats.lastError.at)}: ${s.stats.lastError.message}`, 'error'));
  }

  const actions = el('div', undefined, 'actions');
  if (s.service.ok) {
    if (work) {
      const label = work.auto ? 'Isključi automatsko' : 'Uključi automatsko';
      actions.append(button(label, () => void ask<Result<unknown>>({ type: 'auto', enabled: !work.auto }).then(load), true));
    }
    if (searchUrl) {
      actions.append(
        button('Pokupi ovu pretragu', () => void ask<Result<unknown>>({ type: 'start-run', kind: 'search', pageUrl: searchUrl }).then(load), true),
      );
    }
    actions.append(button('Pokreni ceo sajt', () => void ask<Result<unknown>>({ type: 'start-run', kind: 'full' }).then(load), true));
    if (work?.paused_until) actions.append(button('Nastavi', () => void ask({ type: 'pause', hours: 0 }).then(load), true));
    else actions.append(button('Pauziraj 24 h', () => void ask({ type: 'pause', hours: 24 }).then(load), true));
    if (s.queued > 0) actions.append(button('Pošalji red', () => void ask({ type: 'flush' }).then(load), true));
  }
  app.append(actions);
}

async function load(): Promise<void> {
  try {
    const [status, searchUrl] = await Promise.all([ask<StatusResponse>({ type: 'status' }), currentSearchUrl()]);
    render(status, searchUrl);
  } catch (error) {
    app.replaceChildren(el('p', `Greška: ${String(error)}`, 'error'));
  }
}

void load();
