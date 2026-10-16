import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {UpdateChecker, compareVersions, parseVersion, selectRelease, REPOSITORY} from '../app/updates.mjs';

const tempRoot = process.env.PVZ_TEST_ARTIFACTS || (process.platform === 'win32' ?
  'D:/Temp/Codex/待删除/PvZ-MCP-update-tests' : path.join(os.tmpdir(), 'pvz-update-tests'));
fs.mkdirSync(tempRoot, {recursive: true});
const folder = () => fs.mkdtempSync(path.join(tempRoot, 'case-'));
const start = Date.parse('2026-10-16T00:00:00Z');
function release(version, extra = {}) {
  const tag = 'v' + version, filename = `PvZ-MCP-${version}-Setup.exe`;
  return {tag_name: tag, published_at: '2026-10-16T00:00:00Z', draft: false,
    prerelease: version.includes('-'), assets: [{name: filename, state: 'uploaded', size: 54321152,
      digest: 'sha256:' + 'a'.repeat(64),
      browser_download_url: `https://github.com/${REPOSITORY}/releases/download/${tag}/${filename}`}], ...extra};
}
function checker(options = {}) {
  return new UpdateChecker({version: '0.3.1-preview.1', env: {}, dataDir: folder(), now: () => start, ...options});
}

test('semantic ordering handles numeric prereleases, stable versions and backports', () => {
  const versions = ['0.3.2-preview.2', '0.3.2-preview.10', '0.3.2', '0.3.10', '0.4.0-alpha', '0.4.0'];
  for (let i = 1; i < versions.length; i++) assert.ok(compareVersions(versions[i - 1], versions[i]) < 0);
  assert.equal(compareVersions('v1.2.3+build.1', '1.2.3+build.2'), 0);
  assert.equal(compareVersions('1.0.0-9999999999999999999999', '1.0.0-10000000000000000000000'), -1);
  for (const invalid of ['1.02.3', '1.2.3-preview.01', 'latest', '../1.2.3', null]) assert.equal(parseVersion(invalid), null);
});

test('preview channel includes stable and preview releases; stable excludes both preview signals', () => {
  const rows = [release('0.3.1'), release('0.3.2-preview.1'), release('0.3.3', {prerelease: true})];
  assert.equal(selectRelease(rows, 'stable').version, '0.3.1');
  assert.equal(selectRelease(rows, 'preview').version, '0.3.3');
  rows.push(release('0.3.4-preview.1', {prerelease: false}));
  assert.equal(selectRelease(rows, 'stable').version, '0.3.1');
});

test('drafts, incomplete uploads, missing installers and foreign download URLs are excluded', () => {
  const foreign = release('9.0.0'); foreign.assets[0].browser_download_url = 'https://example.com/malware.exe';
  const another = release('8.0.0'); another.assets[0].browser_download_url = another.assets[0].browser_download_url.replace(REPOSITORY, 'other/repo');
  const pending = release('7.0.0'); pending.assets[0].state = 'new';
  const rows = [foreign, another, pending, release('6.0.0', {draft: true}), release('5.0.0', {assets: []}),
    release('4.0.0', {published_at: null}), release('3.0.0', {assets: 'invalid'}), release('0.3.1')];
  assert.equal(selectRelease(rows, 'preview').version, '0.3.1');
  assert.equal(selectRelease([], 'preview'), null);
});

test('highest version wins even when a lower version has a later publication date', () => {
  assert.equal(selectRelease([release('1.0.0', {published_at: '2029-01-01'}), release('2.0.0')], 'stable').version, '2.0.0');
});

test('automatic checks share a request, cache for 24 hours, and permit a manual refresh', async () => {
  let count = 0, time = start, resolve;
  const pending = new Promise(done => { resolve = done; });
  const c = checker({now: () => time, fetcher: async () => { count++; await pending; return [release('0.3.2-preview.1')]; }});
  const a = c.check({automatic: true}), b = c.check({force: true});
  await Promise.resolve(); assert.equal(c.snapshot().status, 'checking'); resolve();
  assert.equal((await a).available, true); await b; assert.equal(count, 1);
  assert.equal((await c.check()).cached, true); assert.equal(count, 1);
  time += 24 * 60 * 60 * 1000; await c.check(); assert.equal(count, 2);
  await c.check({force: true}); assert.equal(count, 3); c.close();
});

test('cached availability is recalculated after an upgrade or rollback', async () => {
  const dataDir = folder();
  const a = checker({dataDir, fetcher: async () => [release('0.3.2-preview.1')]});
  await a.check(); a.close();
  const b = checker({dataDir, version: '0.3.2-preview.1', fetcher: () => { throw new Error('Must use cache'); }});
  assert.equal((await b.check()).available, false); assert.equal(b.snapshot().latest.version, '0.3.2-preview.1'); b.close();
  const c = checker({dataDir}); assert.equal(c.snapshot().available, true); c.close();
});

test('offline errors preserve the last successful result and back off for one hour', async () => {
  let time = start, failed = false, count = 0;
  const c = checker({now: () => time, fetcher: () => { count++; if (failed) throw new Error('sensitive proxy credentials'); return [release('0.3.2-preview.1')]; }});
  await c.check(); failed = true;
  const result = await c.check({force: true});
  assert.equal(result.status, 'error'); assert.equal(result.stale, true); assert.equal(result.available, true);
  assert.equal(result.error, 'Unable to reach GitHub');
  await c.check(); assert.equal(count, 2);
  time += 60 * 60 * 1000; await c.check(); assert.equal(count, 3); c.close();
});

test('GitHub rate-limit retry interval is respected', async () => {
  let time = start, count = 0;
  const c = checker({now: () => time, fetcher: async () => { count++; throw Object.assign(new Error('GitHub HTTP 403'), {retryMs: 3 * 60 * 60 * 1000}); }});
  await c.check(); time += 2 * 60 * 60 * 1000; await c.check(); assert.equal(count, 1);
  time += 60 * 60 * 1000; await c.check(); assert.equal(count, 2); c.close();
});

test('automatic checks can be disabled; explicit manual checks still work', async () => {
  let count = 0;
  const c = checker({env: {PVZ_UPDATE_CHECK: '0'}, fetcher: async () => { count++; return []; }});
  c.start(); await c.check({automatic: true}); assert.equal(count, 0);
  assert.equal((await c.check({force: true})).status, 'no_release'); assert.equal(count, 1); c.close();
});

test('saved settings control the channel and survive new checker instances', async () => {
  const dataDir = folder();
  fs.writeFileSync(path.join(dataDir, 'update-settings.json'), JSON.stringify({enabled: false, channel: 'stable'}));
  const c = checker({dataDir, fetcher: async () => [release('0.3.2-preview.1')]});
  assert.equal(c.snapshot().enabled, false); assert.equal((await c.check({force: true})).latest, null); c.close();
  const d = checker({dataDir, env: {PVZ_UPDATE_CHANNEL: 'preview'}}); assert.equal(d.snapshot().channel, 'preview'); d.close();
});

test('invalid cache data cannot prevent the server from starting', () => {
  for (const value of ['not-json', 'null', '[]', '{"channel":"preview","last_success":1e99,"release":{"assets":null}}']) {
    const dataDir = folder(); fs.writeFileSync(path.join(dataDir, 'update-cache.json'), value);
    const c = checker({dataDir}); assert.equal(c.snapshot().available, false); c.close();
  }
});

test('shutting down aborts an in-flight check without persisting an artificial network error', async () => {
  const c = checker({fetcher: ({signal}) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')), {once: true});
  })});
  const pending = c.check(); await Promise.resolve(); c.close(); await pending;
  assert.equal(c.snapshot().error, null);
});

test('cache write failures do not fail the metadata check', async () => {
  const dataDir = path.join(folder(), 'not-a-directory'); fs.writeFileSync(dataDir, 'kept');
  const c = checker({dataDir, fetcher: async () => [release('0.3.2-preview.1')]});
  assert.equal((await c.check()).available, true); c.close();
});
