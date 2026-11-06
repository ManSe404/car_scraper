import { defineConfig } from 'wxt';

const SITE = 'https://www.polovniautomobili.com/*';

export default defineConfig({
  manifest: {
    name: 'Car Watch',
    description: 'Povlači oglase sa polovniautomobili.com u tvoju lokalnu Car Watch bazu za analizu cena.',
    // Public key that pins the extension id to anfomkmnpkpjpjdlpngmmkedbplleiam, the only id the Car Watch host
    // accepts (service/src/carwatch/install.py: EXTENSION_ID; a test there checks the two agree). Loading the
    // extension unpacked needs no private key.
    key: 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA9P4IicXv4qz5cF0kMPAvYwVyKBh0lyL6yp9IQVU10O5fELFqi4JwNx/PAg/NPIEB0mFnK/I3XN+Q/9OrB8t6dI40FaEU3YWBoXzibejoWbTZW6sZf7BkKG1gBtuoTmcqyliTuDS5dXf1oNnJfaeZK7DOFPrkOS4YRbFjxmd6orz69ilLOkAiBaVVxaHkV59C0Y8WbKgthysxW8ALLP+0xcOdH86LM19IiKDyvNiDG1ZZ81Ge+oH/BP4lx+5cP6i4ea1kKH5mc43pzECY2NncIIT2kcLJEqc/GYlRp1S46psnLoZMT8w89FHgTrxrJYgdIqGcDzOv2HwATWLYyedCCwIDAQAB',
    // alarms: check for work every 10 minutes; activeTab: read the open search when you press "Pokupi ovu pretragu";
    // nativeMessaging: talk to the Car Watch host on this computer.
    permissions: ['storage', 'alarms', 'activeTab', 'nativeMessaging'],
    web_accessible_resources: [{ resources: ['page-hook.js'], matches: [SITE] }],
  },
  webExt: { disabled: true },
});
