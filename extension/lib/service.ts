/**
 * Talks to Car Watch on this computer through native messaging. Runs in the background service worker only.
 *
 * Chrome starts the host program (service/: carwatch-host) for every message and hands back its one reply; only this
 * extension's id may call it (service/src/carwatch/install.py). Captures made while the host is missing or failing to
 * start wait in an outbox (extension storage) and are sent later.
 */
import { browser } from 'wxt/browser';
import type { Capture } from './extract';
import type {
  CaptureResponse,
  HostReply,
  HostRequest,
  IngestResult,
  Result,
  ServiceErrorKind,
  Stats,
  StatusResponse,
  TaskFailure,
  WorkBatch,
  WorkStatus,
} from './messages';

const HOST_NAME = 'io.github.manse404.carwatch';
// The host answers in well under a second; a valuation runs in its own process and does not delay the reply.
const TIMEOUT_MS = 30_000;
const OUTBOX_LIMIT = 100;

class ServiceError extends Error {
  constructor(
    message: string,
    readonly kind: ServiceErrorKind,
  ) {
    super(message);
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`nema odgovora posle ${ms / 1000} s`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** Send one request to the host; offline when it cannot answer now (worth retrying), rejected when it refuses. */
async function host<T>(request: HostRequest): Promise<T> {
  let reply: HostReply<T> | undefined;
  try {
    reply = (await withTimeout(browser.runtime.sendNativeMessage(HOST_NAME, request), TIMEOUT_MS)) as HostReply<T>;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/not found/i.test(message)) {
      throw new ServiceError('Car Watch nije instaliran na ovom računaru', 'offline');
    }
    throw new ServiceError(`Car Watch ne odgovara: ${message}`, 'offline');
  }
  if (!reply) throw new ServiceError('Car Watch nije vratio odgovor', 'offline');
  if (!reply.ok) {
    throw new ServiceError(`Car Watch: ${reply.error}`, reply.retry ? 'offline' : 'rejected');
  }
  return reply.value;
}

const post = (capture: Capture): Promise<IngestResult> => host<IngestResult>({ type: 'capture', capture });

// ---- stats and outbox (extension storage) -----------------------------------------------------

const today = (): string => new Date().toISOString().slice(0, 10);

async function readStats(): Promise<Stats> {
  const { stats } = (await browser.storage.local.get('stats')) as { stats?: Stats };
  return stats ?? { captures: 0, today: { date: today(), captures: 0 }, lastCaptureAt: null, lastError: null };
}

async function writeStats(update: (stats: Stats) => void): Promise<void> {
  const stats = await readStats();
  if (stats.today.date !== today()) stats.today = { date: today(), captures: 0 };
  update(stats);
  await browser.storage.local.set({ stats });
}

export async function recordError(message: string): Promise<void> {
  await writeStats((s) => {
    s.lastError = { at: new Date().toISOString(), message };
  });
}

async function readOutbox(): Promise<Capture[]> {
  const { outbox } = (await browser.storage.local.get('outbox')) as { outbox?: Capture[] };
  return outbox ?? [];
}

async function enqueue(capture: Capture): Promise<void> {
  const outbox = await readOutbox();
  outbox.push(capture);
  await browser.storage.local.set({ outbox: outbox.slice(-OUTBOX_LIMIT) });
}

let flushing = false;

/** Send queued captures oldest first; stop at the first one the service cannot take yet. */
export async function flushOutbox(): Promise<number> {
  if (flushing) return 0;
  flushing = true;
  let sent = 0;
  try {
    for (;;) {
      const outbox = await readOutbox();
      const next = outbox[0];
      if (!next) break;
      try {
        await post(next);
        sent += 1;
      } catch (error) {
        if (error instanceof ServiceError && error.kind === 'rejected') {
          await recordError(`Odbačen snimak iz reda: ${error.message}`);
        } else {
          break;
        }
      }
      const current = await readOutbox();
      await browser.storage.local.set({ outbox: current.slice(1) });
    }
  } finally {
    flushing = false;
  }
  return sent;
}

// ---- public API for the background script ------------------------------------------------------

export async function sendCapture(capture: Capture, warnings: string[]): Promise<CaptureResponse> {
  try {
    const result = await post(capture);
    await writeStats((s) => {
      s.captures += 1;
      s.today.captures += 1;
      s.lastCaptureAt = new Date().toISOString();
      const [warning] = warnings;
      if (warning !== undefined) s.lastError = { at: new Date().toISOString(), message: warning };
    });
    void flushOutbox();
    return { ok: true, result };
  } catch (error) {
    const err = error instanceof ServiceError ? error : new ServiceError(String(error), 'rejected');
    const queued = err.kind !== 'rejected';
    if (queued) await enqueue(capture);
    await recordError(err.message);
    return { ok: false, kind: err.kind, error: err.message, queued };
  }
}

async function request<T>(message: HostRequest): Promise<Result<T>> {
  try {
    return { ok: true, value: await host<T>(message) };
  } catch (error) {
    const err = error instanceof ServiceError ? error : new ServiceError(String(error), 'rejected');
    return { ok: false, kind: err.kind, error: err.message };
  }
}

export const nextWork = (): Promise<Result<WorkBatch>> => request<WorkBatch>({ type: 'work-next', limit: 10 });

export const failTask = (taskId: number, failure: TaskFailure): Promise<Result<{ ok: boolean }>> =>
  request({ type: 'work-fail', task_id: taskId, failure });

export const startRun = (kind: 'full' | 'search', pageUrl?: string): Promise<Result<{ run_id: number }>> =>
  request({ type: 'start-run', kind, page_url: pageUrl ?? null });

export const pauseWork = (hours: number): Promise<Result<{ ok: boolean }>> => request({ type: 'pause', hours });

export const setAuto = (enabled: boolean): Promise<Result<{ ok: boolean }>> => request({ type: 'auto', enabled });

interface HostStatus {
  version: string;
  ads: number;
  captures: number;
  work: WorkStatus;
}

export async function workStatus(): Promise<Result<WorkStatus>> {
  const result = await request<HostStatus>({ type: 'status' });
  return result.ok ? { ok: true, value: result.value.work } : result;
}

export async function status(workerActive: boolean): Promise<StatusResponse> {
  const result = await request<HostStatus>({ type: 'status' });
  if (result.ok) void flushOutbox();
  return {
    service: result.ok
      ? { ok: true, ads: result.value.ads, captures: result.value.captures }
      : { ok: false, kind: result.kind, error: result.error },
    work: result.ok ? result.value.work : null,
    workerActive,
    stats: await readStats(),
    queued: (await readOutbox()).length,
  };
}
