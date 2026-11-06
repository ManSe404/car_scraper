import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  adUrl,
  extractFromPageProps,
  pageUrlFromDataUrl,
  scrubContacts,
  slugify,
} from '../lib/extract';

const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(join(__dirname, 'fixtures', name), 'utf8'));

const AT = '2026-11-04T20:00:00.000Z';
const SEARCH_URL =
  'https://www.polovniautomobili.com/auto-oglasi/pretraga?brand=Nissan&model%5B%5D=Juke&priceTo=8000&showOldNew=all&yearFrom=2013';
const AD_URL = 'https://www.polovniautomobili.com/auto-oglasi/10000003/nissan-juke-15dcinavi';

// The service's tests read these files, so both sides are checked against the same contract.
const CONTRACT = join(__dirname, '..', '..', 'service', 'tests', 'fixtures');

const SELLER_MARKERS = [
  '"owner"',
  '"dealer"',
  'sellerName',
  'dealerLogo',
  'dealerUrlAlias',
  'contactDetails',
  'profilePhoto',
  'Petar',
  'Auto Plac',
  'Marko',
  'Ulica',
  '777001',
  '555001',
  '7001',
  '064 111',
  '064 123',
  'example.com',
];

describe('search page', () => {
  const result = extractFromPageProps(fixture('site_search_pageprops.json'), SEARCH_URL, AT, 'test');

  it('keeps valid results and reports the skipped one', () => {
    expect(result?.ok).toBe(true);
    if (!result?.ok) return;
    expect(result.capture.kind).toBe('search');
    expect(result.capture.items.map((i) => i.id)).toEqual([10000001, 10000002]);
    expect(result.warnings).toHaveLength(1);
    expect(result.capture.search).toEqual({
      query: { brand: 'Nissan', model: ['Juke'], showOldNew: 'all', yearFrom: 2013, sort: 'basic', priceTo: 8000 },
      page: 1,
      page_count: 3,
      total_items: 61,
    });
  });

  it('maps car fields and derives the seller type without keeping seller data', () => {
    if (!result?.ok) throw new Error('extract failed');
    const [privateAd, dealerAd] = result.capture.items;
    expect(privateAd).toMatchObject({
      title: 'Nissan Juke 1.5 dci',
      url: 'https://www.polovniautomobili.com/auto-oglasi/10000001/nissan-juke-15-dci',
      mileage_km: 223854,
      power_kw: 81,
      horse_power: 110,
      price: 6399,
      base_price: 6899,
      discount_pct: 7,
      is_dealer: false,
      featured: true,
      tags: ['Na ime kupca'],
    });
    expect(dealerAd).toMatchObject({ is_dealer: true, discount_pct: null, featured: true, city: 'Novi Sad' });
    const json = JSON.stringify(result.capture);
    for (const marker of SELLER_MARKERS) expect(json).not.toContain(marker);
  });

  it('matches the service contract fixture', async () => {
    if (!result?.ok) throw new Error('extract failed');
    await expect(JSON.stringify(result.capture, null, 2) + '\n').toMatchFileSnapshot(
      join(CONTRACT, 'capture_search.json'),
    );
  });
});

describe('ad page', () => {
  const result = extractFromPageProps(fixture('site_ad_pageprops.json'), AD_URL, AT, 'test');

  it('keeps the details, the canonical url and the seller type', () => {
    expect(result?.ok).toBe(true);
    if (!result?.ok) return;
    const ad = result.capture.ad!;
    expect(result.capture.kind).toBe('ad');
    expect(ad).toMatchObject({
      id: 10000003,
      url: AD_URL,
      is_dealer: true,
      city: 'Beograd',
      damaged: 'Nije oštećen',
      registration: 'Nije registrovan',
      country_origin: 'Francuska',
      equipment: ['Metalik boja', 'Servo volan', 'Multifunkcionalni volan', 'Tempomat'],
      images_count: 2,
      published_at: '2026-10-07 12:42:40',
      registration_cost_min: 25000,
      registration_cost_max: 32000,
    });
  });

  it('removes phone numbers and e-mail addresses from the description', () => {
    if (!result?.ok) throw new Error('extract failed');
    expect(result.capture.ad!.description).toBe(
      'Auto u odličnom stanju, servisiran 2025. Zvati na [telefon] ili pisati na [email]. Kilometraža 188 266 km.',
    );
    const json = JSON.stringify(result.capture);
    for (const marker of SELLER_MARKERS) expect(json).not.toContain(marker);
  });

  it('matches the service contract fixture', async () => {
    if (!result?.ok) throw new Error('extract failed');
    await expect(JSON.stringify(result.capture, null, 2) + '\n').toMatchFileSnapshot(
      join(CONTRACT, 'capture_ad.json'),
    );
  });
});

describe('site changes are reported, not swallowed', () => {
  it('fails when every search result is unreadable', () => {
    const result = extractFromPageProps(
      { searchResults: { results: [{ id: 'nope' }], page: 1, pageCount: 1, totalItems: 1 } },
      SEARCH_URL,
      AT,
      'test',
    );
    expect(result).toMatchObject({ ok: false });
  });

  it('fails when searchResults changes shape', () => {
    const result = extractFromPageProps({ searchResults: { items: [] } }, SEARCH_URL, AT, 'test');
    expect(result).toMatchObject({ ok: false });
  });

  it('fails when the ad has no id', () => {
    const result = extractFromPageProps({ productData: { title: 'x' } }, AD_URL, AT, 'test');
    expect(result).toMatchObject({ ok: false });
  });

  it('ignores pages that are neither a search nor an ad', () => {
    expect(extractFromPageProps({ articles: [] }, 'https://www.polovniautomobili.com/', AT, 'test')).toBeNull();
    expect(extractFromPageProps(null, 'https://www.polovniautomobili.com/', AT, 'test')).toBeNull();
  });

  it('accepts an empty search', () => {
    const result = extractFromPageProps(
      { searchResults: { results: [], page: 1, pageCount: 0, totalItems: 0 } },
      SEARCH_URL,
      AT,
      'test',
    );
    expect(result).toMatchObject({ ok: true, capture: { items: [] } });
  });
});

describe('helpers', () => {
  it('maps Next.js data urls back to page urls', () => {
    expect(
      pageUrlFromDataUrl('/_next/data/build-abc/sr/auto-oglasi/pretraga.json?brand=Nissan&page=2'),
    ).toBe('https://www.polovniautomobili.com/auto-oglasi/pretraga?brand=Nissan&page=2');
    expect(
      pageUrlFromDataUrl(
        'https://www.polovniautomobili.com/_next/data/build-abc/sr/auto-oglasi/10000003/nissan-juke.json',
      ),
    ).toBe('https://www.polovniautomobili.com/auto-oglasi/10000003/nissan-juke');
    expect(pageUrlFromDataUrl('https://www.polovniautomobili.com/api/v1/delta/search')).toBeNull();
  });

  it('builds slugs the way the site does', () => {
    expect(slugify('Nissan Juke 1.5DCI/N.A.V.I/')).toBe('nissan-juke-15dcinavi');
    expect(slugify('Nissan Juke 1.5 dci')).toBe('nissan-juke-15-dci');
    expect(slugify('Škoda Octavia 1.9 TDI')).toBe('skoda-octavia-19-tdi');
    expect(slugify('Đir')).toBe('djir');
    expect(adUrl(5, '!!!')).toBe('https://www.polovniautomobili.com/auto-oglasi/5/oglas');
  });

  it('scrubs Serbian phone numbers but leaves years, mileage and prices', () => {
    expect(scrubContacts('Pozovite +381 64 123 4567')).toBe('Pozovite [telefon]');
    expect(scrubContacts('tel: 064/123-4567')).toBe('tel: [telefon]');
    expect(scrubContacts('fiksni 011 2345 678.')).toBe('fiksni [telefon].');
    expect(scrubContacts('00381641234567')).toBe('[telefon]');
    expect(scrubContacts('godište 2013, 188 266 km, cena 6.399 €')).toBe('godište 2013, 188 266 km, cena 6.399 €');
    expect(scrubContacts('modeli 2013-2015')).toBe('modeli 2013-2015');
    expect(scrubContacts('mail: ime.prezime+auto@gmail.com')).toBe('mail: [email]');
  });
});
