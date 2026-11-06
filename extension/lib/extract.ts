/**
 * Turns the site's own Next.js page data into the Car Watch capture contract.
 *
 * Only an allow-list of car fields is kept. Seller data (owner, dealer, seller name, contact details)
 * never leaves the browser, and phone numbers / e-mail addresses are removed from ad descriptions.
 * The service side of this contract is service/src/carwatch/models.py.
 */
import { z } from 'zod';

export const SITE_ORIGIN = 'https://www.polovniautomobili.com';

// ---- site shapes (what polovniautomobili.com sends) -------------------------------------------

/** Numbers sometimes arrive as strings (search result ids are "30309447"). */
const num = z.union([
  z.number(),
  z
    .string()
    .trim()
    .regex(/^-?\d+(\.\d+)?$/)
    .transform(Number),
]);
const optNum = num.nullish();
const optStr = z.string().nullish();
const optBool = z.boolean().nullish();
const strings = z
  .array(z.unknown())
  .nullish()
  .transform((list) => (list ?? []).filter((x): x is string => typeof x === 'string'));

const SiteSearchResult = z.object({
  id: num,
  title: z.string(),
  linkFormTitle: optStr,
  brand: optStr,
  model: optStr,
  year: optNum,
  chassis: optStr,
  engineVolume: optNum,
  fuel: optStr,
  mileage: optNum,
  gearBox: optStr,
  doors: optStr,
  seats: optStr,
  power: optNum,
  horsePower: optNum,
  price: optNum,
  basePrice: optNum,
  priceCurrency: optStr,
  discount: optBool,
  discountPercentage: optNum,
  status: optStr,
  conditionNew: optBool,
  registrationInPrice: optBool,
  city: optStr,
  imageMain: optStr,
  imagesNumber: optNum,
  tags: strings,
  updatedAt: optStr,
  searchLiftedAt: optStr,
  featuredSearch: optBool,
  featuredTopSearch: optBool,
  featuredHome: optBool,
  featuredXL: optBool,
  // Read only to derive is_dealer; the value itself is dropped.
  dealer: z.unknown().optional(),
});
type SiteSearchResult = z.infer<typeof SiteSearchResult>;

const SiteSearchPage = z.object({
  searchResults: z.object({
    totalItems: optNum,
    page: optNum,
    pageCount: optNum,
    results: z.array(z.unknown()),
  }),
  jsonFromQuery: z.record(z.string(), z.unknown()).nullish(),
});

const SiteAd = z.object({
  id: num,
  title: z.string(),
  brand: optStr,
  model: optStr,
  mark: optStr,
  year: optNum,
  mileage: optNum,
  price: optNum,
  basePrice: optNum,
  priceCurrency: optStr,
  priceFixed: optBool,
  priceOffer: optBool,
  discount: optBool,
  discountPercentage: optNum,
  status: optStr,
  reserved: optBool,
  conditionNew: optBool,
  condition: strings,
  chassis: optStr,
  color: optStr,
  interiorColor: optStr,
  interiorMaterial: optStr,
  doors: optStr,
  seats: optStr,
  drive: optStr,
  gearBox: optStr,
  fuel: optStr,
  engineVolume: optNum,
  power: optNum,
  horsePower: optNum,
  emissionClass: optStr,
  airCondition: optStr,
  damaged: optStr,
  registration: optStr,
  registrationInPrice: optBool,
  origin: optStr,
  countryOrigin: optStr,
  fromAbroad: optBool,
  ownership: optBool,
  trade: optStr,
  wheelSide: optStr,
  equipment: strings,
  safety: strings,
  features: strings,
  description: optStr,
  publishDate: optStr,
  renewDate: optStr,
  images: z.array(z.unknown()).nullish(),
  registrationInfo: z.object({ priceMin: optNum, priceMax: optNum }).nullish(),
  // Read only to derive is_dealer and city; id and profile photo are dropped.
  owner: z.object({ roles: strings, city: optStr }).nullish(),
});
type SiteAd = z.infer<typeof SiteAd>;

const SiteAdPage = z.object({
  productData: z.unknown(),
  canonical: optStr,
});

// ---- contract (what the service receives) ------------------------------------------------------

export interface CaptureItem {
  id: number;
  title: string;
  url: string;
  brand: string | null;
  model: string | null;
  year: number | null;
  mileage_km: number | null;
  fuel: string | null;
  engine_cc: number | null;
  power_kw: number | null;
  horse_power: number | null;
  gearbox: string | null;
  chassis: string | null;
  doors: string | null;
  seats: string | null;
  price: number | null;
  base_price: number | null;
  currency: string | null;
  discount_pct: number | null;
  status: string | null;
  condition_new: boolean | null;
  registration_in_price: boolean | null;
  city: string | null;
  is_dealer: boolean | null;
  image_url: string | null;
  images_count: number | null;
  tags: string[];
  updated_at: string | null;
  lifted_at: string | null;
  featured: boolean;
}

export interface CaptureAd extends CaptureItem {
  mark: string | null;
  color: string | null;
  interior_color: string | null;
  interior_material: string | null;
  drive: string | null;
  emission_class: string | null;
  air_condition: string | null;
  damaged: string | null;
  registration: string | null;
  origin: string | null;
  country_origin: string | null;
  from_abroad: boolean | null;
  ownership: boolean | null;
  reserved: boolean | null;
  price_fixed: boolean | null;
  price_offer: boolean | null;
  trade: string | null;
  wheel_side: string | null;
  equipment: string[];
  safety: string[];
  condition: string[];
  features: string[];
  description: string | null;
  published_at: string | null;
  renewed_at: string | null;
  registration_cost_min: number | null;
  registration_cost_max: number | null;
}

export interface Capture {
  kind: 'search' | 'ad';
  page_url: string;
  captured_at: string;
  extension_version: string;
  /** Set when the capture answers a task from the service (worker tab). */
  task_id?: number;
  search: {
    query: Record<string, unknown>;
    page: number | null;
    page_count: number | null;
    total_items: number | null;
  } | null;
  items: CaptureItem[];
  ad: CaptureAd | null;
}

export type ExtractResult =
  | { ok: true; capture: Capture; warnings: string[] }
  | { ok: false; error: string };

// ---- helpers ------------------------------------------------------------------------------------

const int = (value: number | null | undefined): number | null =>
  value === null || value === undefined || Number.isNaN(value) ? null : Math.round(value);

export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/đ/g, 'dj')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/[\s-]+/g, '-');
}

/** The site finds an ad by id; the slug after it is cosmetic. */
export function adUrl(id: number, title: string): string {
  return `${SITE_ORIGIN}/auto-oglasi/${id}/${slugify(title) || 'oglas'}`;
}

/** Remove e-mail addresses and Serbian phone numbers (0xx…, +381…, 00381…) from free text. */
export function scrubContacts(text: string): string {
  return text
    .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, '[email]')
    .replace(/(?<![\w+])(?:\+381|00381|0)[\d\s/().-]{6,}\d/g, (match) =>
      match.replace(/\D/g, '').length >= 8 ? '[telefon]' : match,
    );
}

function dealerFlag(dealer: unknown): boolean | null {
  if (dealer === undefined) return null;
  return Boolean(dealer);
}

function issue(error: z.ZodError): string {
  const first = error.issues[0];
  return first ? `${first.path.join('.') || '(root)'}: ${first.message}` : 'invalid';
}

/** Map a /_next/data/<build>/<locale>/<path>.json URL back to the page URL the user sees. */
export function pageUrlFromDataUrl(dataUrl: string): string | null {
  const url = new URL(dataUrl, SITE_ORIGIN);
  const match = url.pathname.match(/^\/_next\/data\/[^/]+(?:\/[a-z]{2}(?=\/))?(\/.*)\.json$/);
  if (!match) return null;
  const path = match[1] === '/index' ? '/' : match[1];
  return `${SITE_ORIGIN}${path}${url.search}`;
}

function toItem(r: SiteSearchResult): CaptureItem {
  return {
    id: r.id,
    title: r.title,
    url: adUrl(r.id, r.linkFormTitle ?? r.title),
    brand: r.brand ?? null,
    model: r.model ?? null,
    year: int(r.year),
    mileage_km: int(r.mileage),
    fuel: r.fuel ?? null,
    engine_cc: int(r.engineVolume),
    power_kw: int(r.power),
    horse_power: int(r.horsePower),
    gearbox: r.gearBox ?? null,
    chassis: r.chassis ?? null,
    doors: r.doors ?? null,
    seats: r.seats ?? null,
    price: int(r.price),
    base_price: int(r.basePrice),
    currency: r.priceCurrency ?? null,
    discount_pct: r.discount ? (r.discountPercentage ?? null) : null,
    status: r.status ?? null,
    condition_new: r.conditionNew ?? null,
    registration_in_price: r.registrationInPrice ?? null,
    city: r.city ?? null,
    is_dealer: dealerFlag(r.dealer),
    image_url: r.imageMain ?? null,
    images_count: int(r.imagesNumber),
    tags: r.tags,
    updated_at: r.updatedAt ?? null,
    lifted_at: r.searchLiftedAt ?? null,
    featured: Boolean(r.featuredSearch || r.featuredTopSearch || r.featuredHome || r.featuredXL),
  };
}

function toAd(r: SiteAd, canonical: string | null | undefined): CaptureAd {
  return {
    id: r.id,
    title: r.title,
    url: canonical ? `${SITE_ORIGIN}/${canonical.replace(/^\/+/, '')}` : adUrl(r.id, r.title),
    brand: r.brand ?? null,
    model: r.model ?? null,
    year: int(r.year),
    mileage_km: int(r.mileage),
    fuel: r.fuel ?? null,
    engine_cc: int(r.engineVolume),
    power_kw: int(r.power),
    horse_power: int(r.horsePower),
    gearbox: r.gearBox ?? null,
    chassis: r.chassis ?? null,
    doors: r.doors ?? null,
    seats: r.seats ?? null,
    price: int(r.price),
    base_price: int(r.basePrice),
    currency: r.priceCurrency ?? null,
    discount_pct: r.discount ? (r.discountPercentage ?? null) : null,
    status: r.status ?? null,
    condition_new: r.conditionNew ?? null,
    registration_in_price: r.registrationInPrice ?? null,
    city: r.owner?.city ?? null,
    is_dealer: r.owner ? r.owner.roles.includes('ROLE_DEALER') : null,
    image_url: null,
    images_count: r.images ? r.images.length : null,
    tags: [],
    updated_at: null,
    lifted_at: null,
    featured: false,
    mark: r.mark ?? null,
    color: r.color ?? null,
    interior_color: r.interiorColor ?? null,
    interior_material: r.interiorMaterial ?? null,
    drive: r.drive ?? null,
    emission_class: r.emissionClass ?? null,
    air_condition: r.airCondition ?? null,
    damaged: r.damaged ?? null,
    registration: r.registration ?? null,
    origin: r.origin ?? null,
    country_origin: r.countryOrigin ?? null,
    from_abroad: r.fromAbroad ?? null,
    ownership: r.ownership ?? null,
    reserved: r.reserved ?? null,
    price_fixed: r.priceFixed ?? null,
    price_offer: r.priceOffer ?? null,
    trade: r.trade ?? null,
    wheel_side: r.wheelSide ?? null,
    equipment: r.equipment,
    safety: r.safety,
    condition: r.condition,
    features: r.features,
    description: r.description ? scrubContacts(r.description) : null,
    published_at: r.publishDate ?? null,
    renewed_at: r.renewDate ?? null,
    registration_cost_min: int(r.registrationInfo?.priceMin),
    registration_cost_max: int(r.registrationInfo?.priceMax),
  };
}

// ---- entry point --------------------------------------------------------------------------------

/**
 * Build a capture from a page's `pageProps`.
 * Returns null for pages that are neither a search nor an ad (home page, news, terms…).
 */
export function extractFromPageProps(
  pageProps: unknown,
  pageUrl: string,
  capturedAt: string,
  version: string,
): ExtractResult | null {
  if (!pageProps || typeof pageProps !== 'object') return null;
  const props = pageProps as Record<string, unknown>;
  const base = { page_url: pageUrl, captured_at: capturedAt, extension_version: version };

  if ('searchResults' in props) {
    const page = SiteSearchPage.safeParse(props);
    if (!page.success) return { ok: false, error: `pretraga: ${issue(page.error)}` };
    const { searchResults, jsonFromQuery } = page.data;
    const items: CaptureItem[] = [];
    const warnings: string[] = [];
    for (const raw of searchResults.results) {
      const parsed = SiteSearchResult.safeParse(raw);
      if (parsed.success) items.push(toItem(parsed.data));
      else warnings.push(`rezultat preskočen: ${issue(parsed.error)}`);
    }
    if (searchResults.results.length > 0 && items.length === 0) {
      return { ok: false, error: `rezultati pretrage: ${warnings[0]}` };
    }
    return {
      ok: true,
      warnings,
      capture: {
        kind: 'search',
        ...base,
        search: {
          query: jsonFromQuery ?? {},
          page: int(searchResults.page),
          page_count: int(searchResults.pageCount),
          total_items: int(searchResults.totalItems),
        },
        items,
        ad: null,
      },
    };
  }

  if ('productData' in props) {
    const page = SiteAdPage.safeParse(props);
    if (!page.success) return { ok: false, error: `oglas: ${issue(page.error)}` };
    const ad = SiteAd.safeParse(page.data.productData);
    if (!ad.success) return { ok: false, error: `oglas: ${issue(ad.error)}` };
    return {
      ok: true,
      warnings: [],
      capture: { kind: 'ad', ...base, search: null, items: [], ad: toAd(ad.data, page.data.canonical) },
    };
  }

  return null;
}
