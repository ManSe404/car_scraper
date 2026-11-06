import { defineUnlistedScript } from 'wxt/utils/define-unlisted-script';

/**
 * Runs in the page's own JavaScript world and only listens. When the site's code loads its Next.js
 * page data (clicking to page 2, opening an ad), a copy of that JSON is handed to the content script.
 * No extra request is made, and any error here is swallowed so the site keeps working.
 */
export default defineUnlistedScript(() => {
  const script = document.currentScript;
  const page = window as typeof window & { __carwatchHooked?: boolean };
  if (!script || page.__carwatchHooked) return;
  page.__carwatchHooked = true;

  const original = window.fetch;
  window.fetch = async function (this: unknown, ...args: Parameters<typeof fetch>): Promise<Response> {
    const response = await original.apply(this, args);
    try {
      const input = args[0];
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (response.ok && /\/_next\/data\/[^?]+\.json/.test(url)) {
        response
          .clone()
          .text()
          .then(
            (text) =>
              script.dispatchEvent(new CustomEvent('carwatch:page-data', { detail: JSON.stringify({ url, text }) })),
            () => undefined,
          );
      }
    } catch {
      // never break the site
    }
    return response;
  };
});
