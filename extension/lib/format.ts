export const thousands = (value: number): string =>
  Math.round(value)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, '.');

export const money = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : `${thousands(value)} €`;

export const signedMoney = (value: number): string =>
  value === 0 ? '' : `${value > 0 ? '+' : '−'}${thousands(Math.abs(value))} €`;

/** Our timestamps are ISO with a zone; the site's are 'YYYY-MM-DD HH:MM:SS' local time. */
export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const normalized = /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value.replace(' ', 'T');
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function shortDate(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return '—';
  const dd = String(date.getDate()).padStart(2, '0');
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}.${date.getFullYear()}.`;
}

export function shortTime(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return '—';
  return `${shortDate(value)} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

export function daysSince(value: string | null | undefined, now = new Date()): number | null {
  const date = parseDate(value);
  if (!date) return null;
  return Math.max(0, Math.floor((now.getTime() - date.getTime()) / 86_400_000));
}

export const plural = (n: number, one: string, many: string): string => (n % 10 === 1 && n % 100 !== 11 ? one : many);
