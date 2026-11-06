/**
 * Pure helpers for the worker tab: which URL to fetch for a task, and what a response means.
 * The loop that uses them lives in entrypoints/collector.content.ts.
 */
import { SITE_ORIGIN, type Capture } from './extract';
import type { FailureReason, WorkTask } from './messages';

export const WORKER_HASH = '#carwatch-worker';
export const WORKER_URL = `${SITE_ORIGIN}/auto-oglasi/pretraga?showOldNew=all${WORKER_HASH}`;

export interface BuildInfo {
  buildId: string;
  locale: string;
}

/** Read the Next.js build id from the page; data URLs only work with the current build. */
export function readBuildInfo(nextDataText: string | null | undefined): BuildInfo | null {
  if (!nextDataText) return null;
  try {
    const data = JSON.parse(nextDataText) as { buildId?: unknown; locale?: unknown };
    if (typeof data.buildId !== 'string' || data.buildId === '') return null;
    return { buildId: data.buildId, locale: typeof data.locale === 'string' ? data.locale : 'sr' };
  } catch {
    return null;
  }
}

/** The same JSON the site's own code loads when you click to a page. */
export function dataUrl(build: BuildInfo, task: WorkTask): string {
  const query = new URLSearchParams(task.params).toString();
  return `/_next/data/${build.buildId}/${build.locale}${task.path}.json${query ? `?${query}` : ''}`;
}

export function pageUrl(task: WorkTask): string {
  const query = new URLSearchParams(task.params).toString();
  return `${SITE_ORIGIN}${task.path}${query ? `?${query}` : ''}`;
}

export type Verdict =
  | { kind: 'json' }
  | { kind: 'fail'; reason: FailureReason; stop: boolean; reload: boolean };

/**
 * What to do with a response before reading it.
 * 403/429/503 or an HTML page instead of JSON means the site (or Cloudflare) refuses: stop everything.
 * 404 with HTML means the site deployed a new build: reload the tab to learn the new build id.
 */
export function judgeResponse(status: number, contentType: string | null): Verdict {
  const isJson = (contentType ?? '').includes('json');
  if (status === 403 || status === 429 || status === 503) return { kind: 'fail', reason: 'blocked', stop: true, reload: false };
  if (status === 404 && !isJson) return { kind: 'fail', reason: 'retry', stop: false, reload: true };
  if (status === 404) return { kind: 'json' };
  if (status >= 500) return { kind: 'fail', reason: 'error', stop: false, reload: false };
  if (!isJson) return { kind: 'fail', reason: 'blocked', stop: true, reload: false };
  return { kind: 'json' };
}

/** Did the JSON give what the task asked for? A removed ad comes back as notFound, a redirect or a search page. */
export function judgeCapture(task: WorkTask, body: unknown, capture: Capture | null): FailureReason | null {
  const data = (body ?? {}) as { notFound?: unknown; pageProps?: Record<string, unknown> };
  const redirected = Boolean(data.pageProps && ('__N_REDIRECT' in data.pageProps || '__N_REDIRECT_STATUS' in data.pageProps));
  if (data.notFound === true || redirected) return task.kind === 'detail' ? 'gone' : 'drift';
  if (capture === null) return task.kind === 'detail' ? 'gone' : 'drift';
  if (task.kind === 'detail' && capture.kind !== 'ad') return 'gone';
  if (task.kind === 'search' && capture.kind !== 'search') return 'drift';
  return null;
}
