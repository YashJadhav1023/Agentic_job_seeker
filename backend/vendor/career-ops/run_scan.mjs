// Thin runner around career-ops' ATS provider modules (see NOTICE.md). Written for Agentic Job Seeker, not part of upstream.
// stdin : {"entries":[{"name":"Acme","careers_url":"https://boards.greenhouse.io/acme"}], "max_jobs":200}
// stdout: one JSON document {"results":[{"name","provider","jobs":[...],"error"}]}
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadProviders } from './providers/_registry.mjs';
import { resolveProvider } from './providers/_registry.mjs';
import { makeHttpCtx } from './providers/_http.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const input = JSON.parse(raw || '{}');
const maxJobs = Number(input.max_jobs) > 0 ? Number(input.max_jobs) : 300;

const log = console.error;
console.error = () => {};            // provider load warnings must not pollute stdout parsing
const providers = await loadProviders(path.join(here, 'providers'));
console.error = log;

const results = [];
for (const entry of input.entries || []) {
  const out = { name: entry.name, provider: null, jobs: [], error: null };
  try {
    const hit = resolveProvider(entry, providers);
    if (!hit || hit.error) throw new Error(hit?.error || 'no supported job board found for this URL');
    out.provider = hit.provider.id;
    const ctx = { ...makeHttpCtx({}), includeUndated: true };
    const jobs = await hit.provider.fetch(entry, ctx);
    out.jobs = (Array.isArray(jobs) ? jobs : []).slice(0, maxJobs).map(j => ({
      title: j.title || '', url: j.url || '', company: j.company || entry.name, location: j.location || '',
      description: (j.description || '').slice(0, 8000), postedAt: j.postedAt || null, externalId: j.externalId || '',
    })).filter(j => j.title && /^https?:\/\//.test(j.url));
  } catch (e) {
    out.error = String(e && e.message ? e.message : e).slice(0, 300);
  }
  results.push(out);
}
process.stdout.write(JSON.stringify({ results }));
