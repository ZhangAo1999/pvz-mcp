/** SDK-to-SDK stdio integration check. Does not mutate the game. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const sdkRoot = process.env.PVZ_SDK_ROOT || path.join(root,'../node_modules');
const { Client } = await import(pathToFileURL(path.join(sdkRoot, '@modelcontextprotocol/client/dist/index.mjs')).href);
const { StdioClientTransport } = await import(pathToFileURL(path.join(sdkRoot, '@modelcontextprotocol/client/dist/stdio.mjs')).href);
const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'mcp-server.mjs')], cwd: root, env: process.env, stderr: 'pipe' });
const protocol = process.env.PVZ_SMOKE_PROTOCOL;
const clientOptions = protocol ? (protocol >= '2026-07-28'
  ? { supportedProtocolVersions: [protocol], versionNegotiation: { mode: { pin: protocol } } }
  : { supportedProtocolVersions: [protocol] }) : {};
const client = new Client({ name: 'pvz-toolset-smoke-test', version: '0.2.0' }, clientOptions);
const stderr = [];
transport.stderr?.on('data', chunk => stderr.push(chunk.toString()));
const report = { test: 'real MCP stdio SDK integration', protocol: protocol || 'SDK default legacy handshake', backend: process.env.PVZ_BACKEND_PATH || path.join(root, 'pvz.py'), checks: [] };
try {
  await client.connect(transport);
  report.checks.push('initialize/connect');
  const catalog = await client.listTools();
  const names = catalog.tools.map(tool => tool.name).sort();
  assert.deepEqual(names, ['pvz_launch_game', 'pvz_list_modes', 'pvz_start_game', 'pvz_return_to_menu', 'pvz_status', 'pvz_get_state', 'pvz_pause', 'pvz_advance', 'pvz_resume', 'pvz_select_seeds', 'pvz_can_place', 'pvz_place_plant', 'pvz_collect', 'pvz_set_presentation', 'pvz_admin_set_sun', 'pvz_admin_set_progress', 'pvz_set_commentary', 'pvz_get_save_info', 'pvz_check_update'].sort());
  assert.equal(catalog.tools.find(tool => tool.name === 'pvz_status').annotations.readOnlyHint, true);
  report.tools = names;
  assert.equal(catalog.tools.find(tool => tool.name === 'pvz_list_modes').annotations.readOnlyHint, true);
  for (const name of ['pvz_get_state', 'pvz_pause', 'pvz_advance', 'pvz_resume', 'pvz_select_seeds', 'pvz_can_place', 'pvz_place_plant', 'pvz_collect', 'pvz_admin_set_sun', 'pvz_start_game', 'pvz_return_to_menu']) {
    assert.ok(catalog.tools.find(tool => tool.name === name).inputSchema.properties.expected_session_id, `${name} must expose expected_session_id`);
  }
  assert.equal(catalog.tools.find(tool => tool.name === 'pvz_select_seeds').inputSchema.properties.seeds.uniqueItems, true);
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_collect').inputSchema.properties.expected_board_epoch);
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_advance').inputSchema.properties.wait);
  assert.equal(catalog.tools.find(tool => tool.name === 'pvz_set_presentation').inputSchema.properties.speed.maximum, 32);
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_set_presentation').inputSchema.properties.commentary_ms);
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_set_presentation').inputSchema.properties.advance_ms);
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_advance').inputSchema.properties.duration_ms);
  assert.ok(!(catalog.tools.find(tool => tool.name === 'pvz_advance').inputSchema.required || []).includes('ticks'));
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_admin_set_progress').inputSchema.required.includes('expected_save_sha256'));
  assert.ok(catalog.tools.find(tool => tool.name === 'pvz_admin_set_progress').inputSchema.required.includes('expected_session_id'));
  assert.equal(catalog.tools.find(t => t.name === 'pvz_check_update').annotations.openWorldHint, true);
  report.checks.push('all 19 tools, update check, session guards, async advance and guarded progress edit schemas listed');
  const response = await client.callTool({ name: 'pvz_status', arguments: {} });
  assert.ok(!response.isError, JSON.stringify(response));
  report.status = response.structuredContent ?? JSON.parse(response.content.find(item => item.type === 'text').text);
  report.checks.push('pvz_status round trip');
  assert.equal(report.status.update.repository, 'ZhangAo1999/pvz-mcp');
  const update = await client.callTool({name: 'pvz_check_update', arguments: {force: false}});
  assert.ok(!update.isError, JSON.stringify(update));
  report.update = update.structuredContent ?? JSON.parse(update.content.find(item => item.type === 'text').text);
  assert.equal(report.update.repository, 'ZhangAo1999/pvz-mcp');
  report.checks.push('update status and manual update tool round trip');
  assert.equal(catalog.tools.find(t=>t.name==='pvz_get_save_info').annotations.readOnlyHint,true);
  if(report.status.running){
    const saveResponse=await client.callTool({name:'pvz_get_save_info',arguments:{expected_session_id:report.status.session_id}});
    assert.ok(!saveResponse.isError,JSON.stringify(saveResponse));
    report.save_info=saveResponse.structuredContent??JSON.parse(saveResponse.content.find(c=>c.type==='text').text);
    assert.match(report.save_info.save_sha256,/^[a-f0-9]{64}$/);
    assert.equal(report.save_info.save_version,12);
    report.checks.push('read-only save identity/hash round trip');
  }

  const modes = await client.callTool({ name: 'pvz_list_modes', arguments: {} });
  assert.ok(!modes.isError, JSON.stringify(modes));
  const modeCatalog = modes.structuredContent ?? JSON.parse(modes.content.find(item => item.type === 'text').text);
  report.mode_catalog_summary = { keys: Object.keys(modeCatalog), mock: modeCatalog.mock === true };
  report.checks.push('pvz_list_modes round trip');
  const invalid = await client.callTool({ name: 'pvz_advance', arguments: { ticks: 0 } });
  assert.equal(invalid.isError, true);
  report.checks.push('ticks=0 rejected by MCP schema');
  const bothDurations=await client.callTool({name:'pvz_advance',arguments:{ticks:200,duration_ms:2000}});
  assert.equal(bothDurations.isError,true);
  report.checks.push('default duration schema and mutually exclusive tick/game-time overrides validated');
  const extra = await client.callTool({ name: 'pvz_status', arguments: { unexpected: true } });
  assert.equal(extra.isError, true);
  report.checks.push('unknown arguments rejected by MCP schema');
  const invalidLevel = await client.callTool({ name: 'pvz_start_game', arguments: { level: 0 } });
  assert.equal(invalidLevel.isError, true);
  report.checks.push('level=0 rejected before game access');
  const duplicateSeeds = await client.callTool({ name: 'pvz_select_seeds', arguments: { seeds: [0, 0], expected_board_epoch: 1 } });
  assert.equal(duplicateSeeds.isError, true);
  const imitator = await client.callTool({ name: 'pvz_select_seeds', arguments: { seeds: [48], expected_board_epoch: 1 } });
  assert.equal(imitator.isError, true);
  report.checks.push('duplicate seeds and unsupported Imitator rejected before game access');
  report.passed = true;
} finally {
  await client.close();
  await transport.close();
  report.stderr = stderr.join('').slice(-4000);
  if (process.env.PVZ_SMOKE_REPORT) fs.writeFileSync(process.env.PVZ_SMOKE_REPORT, JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report, null, 2));
