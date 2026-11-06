import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { defineConfig } from 'wxt';

const SITE = 'https://www.polovniautomobili.com/*';
const port = Number(process.env.CARWATCH_PORT ?? 8050);
const serviceUrl = `http://127.0.0.1:${port}`;

// The service writes this token on first start (`uv run carwatch token`). The build bakes it in so the
// extension can call the local API; without it every call gets 401 and the popup says so.
function readToken(): string {
  const file =
    process.env.CARWATCH_TOKEN_FILE ??
    join(process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'carwatch', 'token');
  if (!existsSync(file)) {
    console.warn(`[carwatch] token file not found at ${file}; run "uv run carwatch token" in service/ first`);
    return '';
  }
  return readFileSync(file, 'utf8').trim();
}

export default defineConfig({
  manifest: {
    name: 'Car Watch',
    description: 'Povlači oglase sa polovniautomobili.com u tvoj lokalni Car Watch servis za analizu cena.',
    // alarms: check for work every 10 minutes; activeTab: read the open search when you press "Pokupi ovu pretragu".
    permissions: ['storage', 'alarms', 'activeTab'],
    host_permissions: [`${serviceUrl}/*`],
    web_accessible_resources: [{ resources: ['page-hook.js'], matches: [SITE] }],
  },
  vite: () => ({
    define: {
      __CARWATCH_SERVICE__: JSON.stringify(serviceUrl),
      __CARWATCH_TOKEN__: JSON.stringify(readToken()),
    },
  }),
  webExt: { disabled: true },
});
