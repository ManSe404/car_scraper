/**
 * Talks to the local Car Watch service. Runs in the background service worker only.
 * Captures made while the service is down wait in an outbox (extension storage) and are sent later.
 */
import { browser } from 'wxt/browser';
import type { Capture } from './extract';
import type {
  CaptureResponse,
  IngestResult,
  Result,
  ServiceErrorKind,
  Stats,
  StatusResponse,
  TaskFailure,
  WorkBatch,
  WorkStatus,
} from './messages';

export const SERVICE_URL = __CARWATCH_SERVICE__;
const TOKEN = __CARWATCH_TOKEN__;
const TIMEOUT_MS = 5000;
const OUTBOX_LIMIT = 100;

class ServiceError extends Error {
  constructor(
    message: string,
    readonly kind: ServiceErrorKind,
  ) {
    super(message);
  }
}

async function call(path: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${SERVICE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', 'X-Carwatch-Token': TOKEN },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new ServiceError(`Servis nije dostupan na ${SERVICE_URL}`, 'offline');
  }
  if (response.status === 401) throw new ServiceError('Servis odbija token (ponovo izgradi ekstenziju)', 'auth');
  if (!response.ok) {
    const body = (await response.text()).slice(0, 300);
    throw new ServiceError(`Servis je vratio ${response.status}: ${body}`, 'rejected');
  }
  return response;
}

async function post(capture: Capture): Promise<IngestResult> {
  const response = await call('/api/captures', { method: 'POST', body: JSON.stringify(capture) });
  return (await response.json()) as IngestResult;
}

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

async function request<T>(path: string, init: RequestInit = {}): Promise<Result<T>> {
  try {
    const response = await call(path, init);
    return { ok: true, value: (await response.json()) as T };
  } catch (error) {
    const err = error instanceof ServiceError ? error : new ServiceError(String(error), 'rejected');
    return { ok: false, kind: err.kind, error: err.message };
  }
}

export const workStatus = (): Promise<Result<WorkStatus>> => request<WorkStatus>('/api/work/status');

export const nextWork = (): Promise<Result<WorkBatch>> => request<WorkBatch>('/api/work?limit=10');

export const failTask = (taskId: number, failure: TaskFailure): Promise<Result<{ ok: boolean }>> =>
  request(`/api/work/${taskId}/fail`, { method: 'POST', body: JSON.stringify(failure) });

export const startRun = (kind: 'full' | 'search', pageUrl?: string): Promise<Result<{ run_id: number }>> =>
  request('/api/runs', { method: 'POST', body: JSON.stringify({ kind, page_url: pageUrl ?? null }) });

export const pauseWork = (hours: number): Promise<Result<{ ok: boolean }>> =>
  request('/api/work/pause', { method: 'POST', body: JSON.stringify({ hours }) });

export const setAuto = (enabled: boolean): Promise<Result<{ ok: boolean }>> =>
  request('/api/work/auto', { method: 'POST', body: JSON.stringify({ enabled }) });

export async function status(workerActive: boolean): Promise<StatusResponse> {
  let service: StatusResponse['service'];
  try {
    const response = await call('/api/health');
    const body = (await response.json()) as { ads: number; captures: number };
    service = { ok: true, ads: body.ads, captures: body.captures };
    void flushOutbox();
  } catch (error) {
    const err = error instanceof ServiceError ? error : new ServiceError(String(error), 'rejected');
    service = { ok: false, kind: err.kind, error: err.message };
  }
  const work = service.ok ? await workStatus() : null;
  return {
    service,
    work: work?.ok ? work.value : null,
    workerActive,
    stats: await readStats(),
    queued: (await readOutbox()).length,
    serviceUrl: SERVICE_URL,
  };
}
