import { browser } from 'wxt/browser';
import { defineBackground } from 'wxt/utils/define-background';
import type { Message } from '../lib/messages';
import {
  failTask,
  flushOutbox,
  nextWork,
  pauseWork,
  recordError,
  sendCapture,
  startRun,
  status,
  workStatus,
} from '../lib/service';
import { WORKER_URL } from '../lib/worker';

const ALARM = 'carwatch-tick';
const TICK_MINUTES = 10;

/** The pinned background tab that executes work, if one is open. */
async function workerTab(): Promise<number | undefined> {
  const { workerTab: id } = (await browser.storage.session.get('workerTab')) as { workerTab?: number };
  if (typeof id !== 'number') return undefined;
  try {
    await browser.tabs.get(id);
    return id;
  } catch {
    await browser.storage.session.remove('workerTab');
    return undefined;
  }
}

/** Ask the service for work; open the worker tab when there is some and none is open yet. */
async function tick(): Promise<void> {
  if ((await workerTab()) !== undefined) return;
  const work = await workStatus();
  if (!work.ok || !work.value.has_work) return;
  const tab = await browser.tabs.create({ url: WORKER_URL, active: false, pinned: true });
  if (tab.id !== undefined) await browser.storage.session.set({ workerTab: tab.id });
}

async function closeWorker(tabId: number | undefined): Promise<void> {
  const id = tabId ?? (await workerTab());
  await browser.storage.session.remove('workerTab');
  if (id !== undefined) {
    try {
      await browser.tabs.remove(id);
    } catch {
      // already closed
    }
  }
}

async function handle(message: Message, tabId: number | undefined): Promise<unknown> {
  switch (message.type) {
    case 'capture':
      return sendCapture(message.capture, message.warnings);
    case 'drift':
      await recordError(`Sajt je promenio format (${message.error}) na ${message.pageUrl}`);
      return { ok: true };
    case 'status':
      return status((await workerTab()) !== undefined);
    case 'flush':
      return { sent: await flushOutbox() };
    case 'work-next':
      return nextWork();
    case 'work-fail':
      if (message.failure.reason === 'blocked') {
        await recordError(`Sajt je odbio zahtev (${message.failure.status ?? '?'}); povlačenje je pauzirano`);
      }
      return failTask(message.taskId, message.failure);
    case 'work-done':
      await closeWorker(tabId);
      return { ok: true };
    case 'start-run': {
      const result = await startRun(message.kind, message.pageUrl);
      if (result.ok) await tick();
      return result;
    }
    case 'pause': {
      const result = await pauseWork(message.hours);
      if (message.hours > 0) await closeWorker(undefined);
      else await tick();
      return result;
    }
  }
}

export default defineBackground(() => {
  browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    handle(message as Message, sender.tab?.id).then(sendResponse, (error: unknown) =>
      sendResponse({ ok: false, kind: 'rejected', error: String(error), queued: false }),
    );
    return true; // keep the channel open for the async answer
  });

  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM) void tick();
  });

  void browser.alarms.get(ALARM).then((existing) => {
    if (!existing) void browser.alarms.create(ALARM, { delayInMinutes: 1, periodInMinutes: TICK_MINUTES });
  });

  browser.runtime.onStartup.addListener(() => void tick());
  browser.tabs.onRemoved.addListener((id) => {
    void browser.storage.session.get('workerTab').then(({ workerTab: current }) => {
      if (current === id) void browser.storage.session.remove('workerTab');
    });
  });
});
