import { describe, expect, it } from 'vitest';
import type { Capture } from '../lib/extract';
import type { WorkTask } from '../lib/messages';
import { dataUrl, judgeCapture, judgeResponse, pageUrl, readBuildInfo } from '../lib/worker';

const search: WorkTask = {
  id: 1,
  kind: 'search',
  path: '/auto-oglasi/pretraga',
  params: { showOldNew: 'all', priceFrom: '4000', priceTo: '4999', page: '3' },
};
const detail: WorkTask = { id: 2, kind: 'detail', path: '/auto-oglasi/30561020/nissan-juke', params: {} };
const build = { buildId: 'build-abc', locale: 'sr' };

const capture = (kind: 'search' | 'ad'): Capture => ({
  kind,
  page_url: 'x',
  captured_at: 'x',
  extension_version: 'test',
  search: null,
  items: [],
  ad: null,
});

describe('worker urls', () => {
  it('builds the same data URL the site loads on a click', () => {
    expect(dataUrl(build, search)).toBe(
      '/_next/data/build-abc/sr/auto-oglasi/pretraga.json?showOldNew=all&priceFrom=4000&priceTo=4999&page=3',
    );
    expect(dataUrl(build, detail)).toBe('/_next/data/build-abc/sr/auto-oglasi/30561020/nissan-juke.json');
    expect(pageUrl(search)).toBe(
      'https://www.polovniautomobili.com/auto-oglasi/pretraga?showOldNew=all&priceFrom=4000&priceTo=4999&page=3',
    );
  });

  it('reads the build id from __NEXT_DATA__', () => {
    expect(readBuildInfo('{"buildId":"build-x","locale":"sr","props":{}}')).toEqual({ buildId: 'build-x', locale: 'sr' });
    expect(readBuildInfo('{"props":{}}')).toBeNull();
    expect(readBuildInfo('not json')).toBeNull();
    expect(readBuildInfo(null)).toBeNull();
  });
});

describe('responses', () => {
  it('stops on refusals and challenge pages', () => {
    for (const status of [403, 429, 503]) {
      expect(judgeResponse(status, 'text/html')).toMatchObject({ kind: 'fail', reason: 'blocked', stop: true });
    }
    expect(judgeResponse(200, 'text/html; charset=UTF-8')).toMatchObject({ reason: 'blocked', stop: true });
  });

  it('reloads on a stale build and reads JSON otherwise', () => {
    expect(judgeResponse(404, 'text/html')).toMatchObject({ reason: 'retry', reload: true, stop: false });
    expect(judgeResponse(404, 'application/json')).toEqual({ kind: 'json' });
    expect(judgeResponse(200, 'application/json')).toEqual({ kind: 'json' });
    expect(judgeResponse(500, 'application/json')).toMatchObject({ reason: 'error', stop: false });
  });

  it('recognises a removed ad', () => {
    expect(judgeCapture(detail, { notFound: true }, null)).toBe('gone');
    expect(judgeCapture(detail, { pageProps: { __N_REDIRECT: '/auto-oglasi' } }, null)).toBe('gone');
    expect(judgeCapture(detail, { pageProps: {} }, capture('search'))).toBe('gone');
    expect(judgeCapture(detail, { pageProps: {} }, capture('ad'))).toBeNull();
  });

  it('flags a search page that did not come back as a search', () => {
    expect(judgeCapture(search, { pageProps: {} }, null)).toBe('drift');
    expect(judgeCapture(search, { pageProps: {} }, capture('ad'))).toBe('drift');
    expect(judgeCapture(search, { pageProps: {} }, capture('search'))).toBeNull();
  });
});
