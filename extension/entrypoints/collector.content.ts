import { browser } from 'wxt/browser';
import { defineContentScript } from 'wxt/utils/define-content-script';
import { injectScript } from 'wxt/utils/inject-script';
import { removeBadge, showBadge, workerBanner } from '../lib/badge';
import { extractFromPageProps, pageUrlFromDataUrl } from '../lib/extract';
import type { CaptureResponse, Message, Result, TaskFailure, WorkBatch, WorkTask } from '../lib/messages';
import { type BuildInfo, WORKER_HASH, dataUrl, judgeCapture, judgeResponse, pageUrl, readBuildInfo } from '../lib/worker';

async function send<T = CaptureResponse>(message: Message): Promise<T | undefined> {
  try {
    return (await browser.runtime.sendMessage(message)) as T;
  } catch {
    // The extension was reloaded while this tab stayed open.
    return undefined;
  }
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

// ---- passive mode: save what the user looks at ---------------------------------------------------

async function watchPages(version: string): Promise<void> {
  const handle = (pageProps: unknown, url: string): void => {
    const result = extractFromPageProps(pageProps, url, new Date().toISOString(), version);
    if (result === null) return;
    if (!result.ok) {
      void send({ type: 'drift', error: result.error, pageUrl: url });
      return;
    }
    const { capture, warnings } = result;
    void send({ type: 'capture', capture, warnings }).then((response) => {
      if (capture.kind === 'ad' && capture.ad) showBadge(capture.ad, response);
      else removeBadge();
    });
  };

  // Clicks inside the site load page data with fetch; the page hook forwards a copy here.
  await injectScript('/page-hook.js', {
    keepInDom: true,
    modifyScript(script) {
      script.addEventListener('carwatch:page-data', (event) => {
        if (!(event instanceof CustomEvent) || typeof event.detail !== 'string') return;
        try {
          const { url, text } = JSON.parse(event.detail) as { url: string; text: string };
          const forPage = pageUrlFromDataUrl(url);
          if (!forPage) return;
          const data = JSON.parse(text) as { pageProps?: unknown };
          handle(data.pageProps, forPage);
        } catch {
          // not JSON we understand
        }
      });
    },
  });

  // A full page load carries the same data in <script id="__NEXT_DATA__">.
  onReady(() => {
    const tag = document.getElementById('__NEXT_DATA__');
    if (!tag?.textContent) return;
    try {
      const data = JSON.parse(tag.textContent) as { props?: { pageProps?: unknown } };
      handle(data.props?.pageProps, location.href);
    } catch {
      // not JSON we understand
    }
  });
}

// ---- worker mode: the pinned tab the background opens to execute the service's work ----------

type Outcome = 'next' | 'stop' | 'reload';

async function fail(task: WorkTask, failure: TaskFailure): Promise<void> {
  await send<Result<unknown>>({ type: 'work-fail', taskId: task.id, failure });
}

async function runTask(task: WorkTask, build: BuildInfo, version: string): Promise<Outcome> {
  let response: Response;
  try {
    response = await fetch(dataUrl(build, task), { headers: { 'x-nextjs-data': '1' }, credentials: 'same-origin' });
  } catch (error) {
    await fail(task, { reason: 'error', status: null, message: String(error) });
    return 'next';
  }
  const verdict = judgeResponse(response.status, response.headers.get('content-type'));
  if (verdict.kind === 'fail') {
    await fail(task, { reason: verdict.reason, status: response.status, message: null });
    return verdict.stop ? 'stop' : verdict.reload ? 'reload' : 'next';
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    await fail(task, { reason: 'blocked', status: response.status, message: 'odgovor nije JSON' });
    return 'stop';
  }
  const result = extractFromPageProps(
    (body as { pageProps?: unknown } | null)?.pageProps,
    pageUrl(task),
    new Date().toISOString(),
    version,
  );
  if (result !== null && !result.ok) {
    await fail(task, { reason: 'drift', status: response.status, message: result.error });
    return 'next';
  }
  const capture = result?.capture ?? null;
  const problem = judgeCapture(task, body, capture);
  if (problem !== null || capture === null) {
    await fail(task, { reason: problem ?? 'drift', status: response.status, message: null });
    return 'next';
  }
  capture.task_id = task.id;
  await send({ type: 'capture', capture, warnings: result?.ok ? result.warnings : [] });
  return 'next';
}

async function runWorker(version: string): Promise<void> {
  await new Promise<void>((resolve) => onReady(resolve));
  const banner = workerBanner();
  const build = readBuildInfo(document.getElementById('__NEXT_DATA__')?.textContent);
  if (build === null) {
    banner.update('Car Watch: stranica nema podatke koje očekujem; prekidam.');
    await send({ type: 'work-done', reason: 'no build id' });
    return;
  }
  let done = 0;
  for (;;) {
    const batch = await send<Result<WorkBatch>>({ type: 'work-next' });
    if (!batch?.ok || batch.value.tasks.length === 0) break;
    for (const task of batch.value.tasks) {
      await sleep(batch.value.gap_seconds * 1000 + Math.random() * 1000);
      const outcome = await runTask(task, build, version);
      done += 1;
      banner.update(`Car Watch povlači podatke… ${done} strana u ovoj sesiji`);
      if (outcome === 'reload') {
        if (tooManyReloads()) {
          banner.update('Car Watch: sajt stalno menja verziju; prekidam do sledeće provere.');
          await send({ type: 'work-done', reason: 'reload loop' });
        } else {
          location.reload();
        }
        return;
      }
      if (outcome === 'stop') {
        banner.update('Car Watch: sajt je odbio zahtev, povlačenje je pauzirano.');
        await send({ type: 'work-done', reason: 'blocked' });
        return;
      }
    }
  }
  await send({ type: 'work-done', reason: 'no more work' });
}

/** The site deploys a new build now and then; reload to learn it, but never loop. */
function tooManyReloads(): boolean {
  const key = 'carwatch-reloads';
  const now = Date.now();
  let recent: number[] = [];
  try {
    recent = (JSON.parse(sessionStorage.getItem(key) ?? '[]') as number[]).filter((t) => now - t < 10 * 60_000);
    sessionStorage.setItem(key, JSON.stringify([...recent, now]));
  } catch {
    return true;
  }
  return recent.length >= 3;
}

function onReady(callback: () => void): void {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', callback, { once: true });
  else callback();
}

export default defineContentScript({
  matches: ['https://www.polovniautomobili.com/*'],
  runAt: 'document_start',
  async main() {
    const version = browser.runtime.getManifest().version;
    if (location.hash === WORKER_HASH) await runWorker(version);
    else await watchPages(version);
  },
});
