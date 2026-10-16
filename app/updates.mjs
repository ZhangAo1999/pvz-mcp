/** Public release metadata only. Never downloads or executes an installer. */
import fs from 'node:fs';
import https from 'node:https';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const REPOSITORY = 'ZhangAo1999/pvz-mcp';
export const RELEASES_URL = `https://github.com/${REPOSITORY}/releases`;
export const VERSION = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
const DAY = 24 * 60 * 60 * 1000;
const HOUR = DAY / 24;

export function parseVersion(value) {
  if (typeof value !== 'string') return null;
  const match = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/.exec(value);
  if (!match) return null;
  const core = match.slice(1, 4).map(Number), pre = match[4]?.split('.') || [];
  if (core.some(n => !Number.isSafeInteger(n)) || pre.some(s => /^0\d+$/.test(s))) return null;
  return {core, pre, version: value.replace(/^v/, '')};
}

export function compareVersions(left, right) {
  const a = parseVersion(left), b = parseVersion(right);
  if (!a || !b) throw new Error('Invalid semantic version');
  for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return Math.sign(a.core[i] - b.core[i]);
  if (!a.pre.length || !b.pre.length) return Math.sign(b.pre.length - a.pre.length);
  for (let i = 0; i < Math.max(a.pre.length, b.pre.length); i++) {
    const x = a.pre[i], y = b.pre[i];
    if (x === undefined || y === undefined) return x === y ? 0 : x === undefined ? -1 : 1;
    if (x === y) continue;
    const xn = /^\d+$/.test(x), yn = /^\d+$/.test(y);
    if (xn && yn) return BigInt(x) < BigInt(y) ? -1 : 1;
    if (xn !== yn) return xn ? -1 : 1;
    return x < y ? -1 : 1;
  }
  return 0;
}

function sameUrl(value, expected) {
  try {
    const url = new URL(value);
    return !url.username && !url.password && !url.search && !url.hash &&
      decodeURIComponent(url.href) === decodeURIComponent(expected);
  } catch { return false; }
}

export function selectRelease(releases, channel) {
  if (!Array.isArray(releases)) throw new Error('Invalid GitHub release response');
  const choices = [];
  for (const release of releases) {
    if (!release || release.draft || !release.published_at) continue;
    const version = parseVersion(release.tag_name);
    if (!version || (channel === 'stable' && (release.prerelease || version.pre.length))) continue;
    const filename = `PvZ-MCP-${version.version}-Setup.exe`;
    const download = `https://github.com/${REPOSITORY}/releases/download/${encodeURIComponent(release.tag_name)}/${encodeURIComponent(filename)}`;
    const asset = (Array.isArray(release.assets) ? release.assets : []).find(a => a && a.name === filename && a.state === 'uploaded' &&
      Number.isSafeInteger(a.size) && a.size > 0 && sameUrl(a.browser_download_url, download));
    if (!asset) continue;
    choices.push({version: version.version, tag: release.tag_name, prerelease: !!release.prerelease || !!version.pre.length,
      release_url: `${RELEASES_URL}/tag/${encodeURIComponent(release.tag_name)}`,
      download_url: download, asset_name: filename, size: asset.size,
      sha256: /^sha256:[a-f0-9]{64}$/.test(asset.digest) ? asset.digest.slice(7) : null});
  }
  return choices.sort((a, b) => compareVersions(b.version, a.version))[0] || null;
}

export async function fetchReleases({env = process.env, signal, timeoutMs = 8000} = {}) {
  const proxy = env.PVZ_UPDATE_PROXY || env.https_proxy || env.HTTPS_PROXY || env.http_proxy || env.HTTP_PROXY;
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (proxy && (major < 24 || (major === 24 && minor < 5))) throw new Error('Proxy support requires Node.js 24.5 or newer');
  const agent = new https.Agent({proxyEnv: {...env, ...(proxy ? {https_proxy: proxy} : {})}});
  const abort = AbortSignal.any([AbortSignal.timeout(timeoutMs), ...(signal ? [signal] : [])]);
  const all = [];
  try {
    for (let page = 1; page <= 3; page++) {
      const rows = await new Promise((resolve, reject) => {
        const request = https.get(`https://api.github.com/repos/${REPOSITORY}/releases?per_page=100&page=${page}`, {
          agent, signal: abort, headers: {'User-Agent': 'PvZ-MCP-update-check',
            Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'},
        }, response => {
          if (response.statusCode !== 200) {
            response.resume();
            const error = new Error(`GitHub HTTP ${response.statusCode}`);
            const reset = Number(response.headers['x-ratelimit-reset']) * 1000;
            const retry = Number(response.headers['retry-after']) * 1000;
            error.retryMs = Math.max(HOUR, (Number.isFinite(reset) ? reset - Date.now() : 0), retry || 0);
            reject(error); return;
          }
          const chunks = []; let size = 0;
          response.on('data', chunk => {
            size += chunk.length;
            if (size > 2 * 1024 * 1024) request.destroy(new Error('GitHub response exceeds 2 MiB'));
            else chunks.push(chunk);
          });
          response.on('error', reject);
          response.on('end', () => {
            try {
              const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
              if (!Array.isArray(result)) throw new Error('Invalid GitHub release response');
              resolve(result);
            } catch (error) { reject(error); }
          });
        });
        request.on('error', reject);
      });
      all.push(...rows);
      if (rows.length < 100) break;
    }
    return all;
  } finally { agent.destroy(); }
}

function readJson(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export class UpdateChecker {
  constructor({version = VERSION, dataDir, env = process.env, fetcher, now = Date.now, notify = () => {}} = {}) {
    if (!parseVersion(version)) throw new Error('Invalid installed version');
    this.version = version; this.env = env; this.now = now; this.notify = notify;
    this.dataDir = dataDir || env.PVZ_DATA_DIR || path.join(path.dirname(fileURLToPath(import.meta.url)), 'data');
    this.cacheFile = path.join(this.dataDir, 'update-cache.json');
    this.cache = readJson(this.cacheFile);
    for (const key of ['last_success', 'last_attempt']) {
      if (!Number.isFinite(this.cache[key]) || this.cache[key] <= 0 || this.cache[key] > 8e15) delete this.cache[key];
    }
    this.fetcher = fetcher || (options => fetchReleases({...options, env}));
    this.abort = new AbortController();
  }
  settings() {
    const settings = readJson(path.join(this.dataDir, 'update-settings.json'));
    return {enabled: this.env.PVZ_UPDATE_CHECK !== '0' && settings.enabled !== false,
      channel: ['stable', 'preview'].includes(this.env.PVZ_UPDATE_CHANNEL) ? this.env.PVZ_UPDATE_CHANNEL :
        ['stable', 'preview'].includes(settings.channel) ? settings.channel : parseVersion(this.version).pre.length ? 'preview' : 'stable'};
  }
  snapshot() {
    const settings = this.settings(), cache = this.cache.channel === settings.channel ? this.cache : {};
    const latest = cache.release ? selectRelease([cache.release], settings.channel) : null;
    const available = !!latest && compareVersions(latest.version, this.version) > 0;
    return {repository: REPOSITORY, current_version: this.version, ...settings, check_interval_hours: 24,
      status: this.pending ? 'checking' : cache.error ? 'error' : cache.last_success ? latest ? 'ok' : 'no_release' : 'not_checked',
      available, latest, checked_at: cache.last_success ? new Date(cache.last_success).toISOString() : null,
      last_attempt_at: cache.last_attempt ? new Date(cache.last_attempt).toISOString() : null,
      stale: !!cache.error && !!latest, error: cache.error || null, releases_url: RELEASES_URL,
      ...(available ? {notification: `PvZ MCP ${latest.version} is available. Tell the user outside active recording; download the EXE from the release page to upgrade.`} : {})};
  }
  persist() {
    try {
      fs.mkdirSync(this.dataDir, {recursive: true});
      const staged = this.cacheFile + '.' + process.pid + '.new';
      fs.writeFileSync(staged, JSON.stringify(this.cache, null, 2));
      fs.renameSync(staged, this.cacheFile);
    } catch { /* A read-only data directory must not break gameplay. */ }
  }
  async check({force = false, automatic = false} = {}) {
    const settings = this.settings(), now = this.now();
    if (this.closed || (automatic && !settings.enabled)) return this.snapshot();
    if (this.pending) return this.pending;
    const sameChannel = this.cache.channel === settings.channel;
    const interval = this.cache.error ? Math.min(DAY, Math.max(HOUR, this.cache.retry_ms || 0)) : DAY;
    const elapsed = now - this.cache.last_attempt;
    if (!force && sameChannel && elapsed >= 0 && elapsed < interval) return {...this.snapshot(), cached: true};
    this.pending = Promise.resolve().then(async () => {
      try {
        const releases = await this.fetcher({signal: this.abort.signal});
        const latest = selectRelease(releases, settings.channel);
        const original = latest ? releases.find(r => r.tag_name === latest.tag) : null;
        // Keep only metadata necessary to validate and recalculate update availability after an upgrade.
        const release = original ? {tag_name: original.tag_name, published_at: original.published_at,
          prerelease: original.prerelease, assets: original.assets.filter(a => a.name === latest.asset_name).map(a => ({
            name: a.name, state: a.state, size: a.size, digest: a.digest, browser_download_url: a.browser_download_url}))} : null;
        this.cache = {channel: settings.channel, last_attempt: this.now(), last_success: this.now(), release};
      } catch (error) {
        if (this.closed) return this.snapshot();
        this.cache = {...(sameChannel ? this.cache : {}), channel: settings.channel,
          last_attempt: this.now(), error: error.name === 'AbortError' ? 'Update check timed out or was cancelled' :
            /^GitHub |^Invalid GitHub |^Proxy support/.test(error.message) ? error.message : 'Unable to reach GitHub',
          retry_ms: Math.min(DAY, Math.max(HOUR, error.retryMs || 0))};
      } finally { this.pending = null; }
      this.persist();
      const result = this.snapshot();
      if (result.available && !result.error) this.notify(result);
      return result;
    });
    return this.pending;
  }
  start() {
    if (this.timer || this.closed) return;
    void this.check({automatic: true});
    this.timer = setInterval(() => { void this.check({automatic: true}); }, HOUR);
    this.timer.unref();
  }
  close() { this.closed = true; clearInterval(this.timer); this.abort.abort(); }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const checker = new UpdateChecker();
  const result = await checker.check({force: true});
  console.log(JSON.stringify(result, null, 2));
  checker.close();
  if (result.status === 'error') process.exitCode = 1;
}
