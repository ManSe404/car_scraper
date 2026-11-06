import type { Capture } from './extract';

/** What the host answers to a capture (service/src/carwatch/models.py: IngestResult). */
export interface AdSummary {
  known: boolean;
  id: number;
  first_seen: string | null;
  last_seen: string | null;
  days_seen: number;
  first_price: number | null;
  price: number | null;
  min_price: number | null;
  max_price: number | null;
  price_changes: number;
  published_at: string | null;
  ui_url: string | null;
}

export interface IngestResult {
  ok: boolean;
  capture_id: number;
  ads_seen: number;
  new_ads: number;
  price_changes: { ad_id: number; title: string; old_price: number; new_price: number }[];
  summary: AdSummary | null;
}

/** offline: Chrome could not start the host (not installed, crashed); rejected: the host answered with an error. */
export type ServiceErrorKind = 'offline' | 'rejected';

export type CaptureResponse =
  | { ok: true; result: IngestResult }
  | { ok: false; kind: ServiceErrorKind; error: string; queued: boolean };

export interface Stats {
  captures: number;
  today: { date: string; captures: number };
  lastCaptureAt: string | null;
  lastError: { at: string; message: string } | null;
}

export interface StatusResponse {
  service: { ok: true; ads: number; captures: number } | { ok: false; kind: ServiceErrorKind; error: string };
  work: WorkStatus | null;
  workerActive: boolean;
  stats: Stats;
  queued: number;
}

export type Message =
  | { type: 'capture'; capture: Capture; warnings: string[] }
  | { type: 'drift'; error: string; pageUrl: string }
  | { type: 'status' }
  | { type: 'flush' }
  | WorkMessage;

// ---- collection work (service/src/carwatch/models.py: WorkTask, WorkBatch, WorkStatus) -------------

export interface WorkTask {
  id: number;
  kind: 'search' | 'detail';
  path: string;
  params: Record<string, string>;
}

export interface WorkBatch {
  tasks: WorkTask[];
  gap_seconds: number;
  paused_until: string | null;
}

export type FailureReason = 'blocked' | 'gone' | 'retry' | 'drift' | 'error';

export interface TaskFailure {
  reason: FailureReason;
  status: number | null;
  message: string | null;
}

export interface RunProgress {
  id: number;
  kind: 'full' | 'search';
  status: string;
  started_at: string;
  finished_at: string | null;
  tasks_total: number;
  tasks_done: number;
  tasks_failed: number;
  ads_seen: number;
  /** Started by the service on its own (the weekly full pass), not by the user. */
  auto: boolean;
}

export interface WorkStatus {
  has_work: boolean;
  /** Automatic collection: on, the service starts full passes and plans detail pages by itself. */
  auto: boolean;
  paused_until: string | null;
  run: RunProgress | null;
  next_full_run: string | null;
  details_today: number;
  details_limit: number;
}

export type Result<T> = { ok: true; value: T } | { ok: false; kind: ServiceErrorKind; error: string };

export type WorkMessage =
  | { type: 'work-next' }
  | { type: 'work-fail'; taskId: number; failure: TaskFailure }
  | { type: 'work-done'; reason: string }
  | { type: 'start-run'; kind: 'full' | 'search'; pageUrl?: string }
  | { type: 'pause'; hours: number }
  | { type: 'auto'; enabled: boolean };

// ---- native messaging host (service/src/carwatch/host.py: HostRequest, Host.handle) ------------------------

export type HostRequest =
  | { type: 'status' }
  | { type: 'capture'; capture: Capture }
  | { type: 'work-next'; limit: number }
  | { type: 'work-fail'; task_id: number; failure: TaskFailure }
  | { type: 'start-run'; kind: 'full' | 'search'; page_url: string | null }
  | { type: 'pause'; hours: number }
  | { type: 'auto'; enabled: boolean };

/** retry: the request was fine but the host failed (e.g. a locked database); worth sending again later. */
export type HostReply<T> = { ok: true; value: T } | { ok: false; error: string; retry: boolean };
