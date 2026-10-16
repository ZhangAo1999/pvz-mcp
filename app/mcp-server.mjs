/** Local PvZ MCP adapter. Game access lives in pvz.py; stdout is MCP only. */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { UpdateChecker, VERSION } from './updates.mjs';

const root = path.dirname(fileURLToPath(import.meta.url));
const sdkRoot = process.env.PVZ_SDK_ROOT || path.join(root,'../node_modules');
const { McpServer } = await import(pathToFileURL(path.join(sdkRoot, '@modelcontextprotocol/server/dist/index.mjs')).href);
const { serveStdio } = await import(pathToFileURL(path.join(sdkRoot, '@modelcontextprotocol/server/dist/stdio.mjs')).href);
const { z } = await import(pathToFileURL(path.join(sdkRoot, 'zod/index.js')).href);

class BackendError extends Error {
  constructor(code, message, details) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

class Backend {
  child;
  pending;
  sequence = 0;
  queue = Promise.resolve();
  failure;
  closed = false;

  start() {
    if (this.child) return;
    if (this.closed) throw new BackendError('BACKEND_CLOSED', 'The backend connection is closed.');
    if (this.failure) throw this.failure;
    const temp = process.env.PVZ_TEMP || path.join(process.env.PVZ_DATA_DIR || root,'temp');
    fs.mkdirSync(temp, { recursive: true });
    this.child = spawn(
      process.env.PVZ_PYTHON || 'python.exe',
      ['-u', process.env.PVZ_BACKEND_PATH || path.join(root, 'pvz.py'), '--stdio'],
      {
        cwd: root,
        shell: false,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: {
          ...process.env, TEMP: temp, TMP: temp, TMPDIR: temp,
          PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
          PYTHONPYCACHEPREFIX: process.env.PYTHONPYCACHEPREFIX || path.join(process.env.PVZ_DATA_DIR || root,'cache','python'),
        },
      },
    );
    this.child.stderr.on('data', chunk => process.stderr.write(chunk));
    this.child.on('error', error => this.fail(new BackendError('BACKEND_START_FAILED', error.message)));
    this.child.stdin.on('error', error => this.fail(new BackendError('BACKEND_WRITE_FAILED', error.message)));
    this.child.on('exit', (code, signal) => {
      if (!this.closed) this.fail(new BackendError('BACKEND_EXITED', `Backend exited (code=${code}, signal=${signal}).`));
    });
    let bufferedBytes = 0;
    this.child.stdout.on('data', chunk => {
      const lastNewline = chunk.lastIndexOf(10);
      bufferedBytes = lastNewline < 0 ? bufferedBytes + chunk.length : chunk.length - lastNewline - 1;
      if (bufferedBytes > 8 * 1024 * 1024) this.fail(new BackendError('BACKEND_PROTOCOL_ERROR', 'Backend response exceeded 8 MiB.'));
    });
    const lines = readline.createInterface({ input: this.child.stdout, crlfDelay: Infinity });
    lines.on('line', line => {
      if (!line.trim()) return;
      if (Buffer.byteLength(line) > 8 * 1024 * 1024) {
        this.fail(new BackendError('BACKEND_PROTOCOL_ERROR', 'Backend response exceeded 8 MiB.'));
        return;
      }
      let response;
      try { response = JSON.parse(line); }
      catch { this.fail(new BackendError('BACKEND_PROTOCOL_ERROR', 'Backend wrote a non-JSON response.')); return; }
      const pending = this.pending;
      if (!pending || typeof response !== 'object' || response === null || response.id !== pending.id) {
        this.fail(new BackendError('BACKEND_PROTOCOL_ERROR', 'Backend response ID did not match the pending request.'));
        return;
      }
      this.pending = undefined;
      clearTimeout(pending.timer);
      if (Object.hasOwn(response, 'error')) {
        const error = response.error;
        pending.reject(new BackendError(error?.code || 'GAME_ERROR', error?.message || String(error), error?.details ?? error));
      } else if (Object.hasOwn(response, 'result')) {
        pending.resolve(response.result);
      } else {
        pending.reject(new BackendError('BACKEND_PROTOCOL_ERROR', 'Backend response has neither result nor error.'));
      }
    });
  }

  fail(error) {
    this.failure ||= error;
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(this.failure);
      this.pending = undefined;
    }
    this.child?.kill();
  }

  request(method, params, signal) {
    // One request at a time also serializes competing MCP tools/call requests.
    // A cancelled request must never begin later just because it was queued.
    const task = this.queue.then(() => {
      if (signal?.aborted) throw new BackendError('CANCELLED', 'Request cancelled before it was sent to the game backend.', { submitted: false });
      return this.requestOne(method, params);
    });
    this.queue = task.catch(() => {});
    return task;
  }

  requestOne(method, params) {
    if (this.closed) return Promise.reject(new BackendError('BACKEND_CLOSED', 'The backend connection is closed.'));
    if (this.failure) return Promise.reject(this.failure);
    try { this.start(); } catch (error) { return Promise.reject(error); }
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      // Host advancement has its own bounded simulation deadline plus native
      // acknowledgements. Keep this watchdog longer than that full operation.
      const timeoutMs = ['advance', 'launch_game', 'start_game', 'return_to_menu', 'select_seeds'].includes(method) ? 120_000 : 45_000;
      const timer = setTimeout(() => {
        this.fail(new BackendError('BACKEND_TIMEOUT', 'Backend timed out. Reconnect this MCP server, then inspect game state before repeating a mutation.', { outcome_unknown: true, reconnect_required: true }));
      }, timeoutMs);
      this.pending = { id, resolve, reject, timer };
      this.child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.reject(new BackendError('BACKEND_CLOSED', 'The MCP client disconnected.'));
      this.pending = undefined;
    }
    this.child?.stdin.end();
    const child = this.child;
    if (child) setTimeout(() => { if (child.exitCode === null) child.kill(); }, 2000).unref();
  }
}

const backend = new Backend();
const updates = new UpdateChecker({notify: result => console.error(`[pvz-mcp] Update available: ${result.latest.version} ${result.latest.release_url}`)});
const epoch = z.number().int().min(1).max(0xffffffff).describe('board_epoch from the latest pvz_get_state; rejects commands against an old board.');
const session = z.string().min(1).max(128).describe('Optional session_id from status/state; rejects an action if the game process has restarted.').optional();
const commandId = z.string().min(1).max(128).describe('Optional unique action ID. Reuse only to retry the exact same action; backend deduplicates it.').optional();
const mutation = { expected_session_id: session, command_id: commandId };
const speed = z.number().int().min(1).max(32).optional().describe('Simulation speed multiplier; omitted uses the spectator preference (initially 1). Change the preference with pvz_set_presentation.');
const placement = {
  card_slot: z.number().int().min(1).max(10).describe('1-based seed card slot in the current seed bank.'),
  row: z.number().int().min(1).max(6).describe('1-based lawn row; the actual board may have fewer rows.'),
  col: z.number().int().min(1).max(9).describe('1-based lawn column.'),
  expected_board_epoch: epoch,
  expected_session_id: session,
};
const selectedSeeds = z.array(z.number().int().min(0).max(47)).min(1).max(10)
  .refine(seeds => new Set(seeds).size === seeds.length, { message: 'Seed IDs must be unique.' })
  .meta({ uniqueItems: true })
  .describe('Unique plant type IDs from the current available_seeds list, in deck order. Use exactly the deck size required by the game. Imitator (48) is not supported.');
const specifications = [
  ['pvz_check_update', 'check_update', 'Check published installer releases from ZhangAo1999/pvz-mcp. Includes preview releases when the configured channel is preview. force defaults to true; false reuses the 24-hour cache. Returns version, release page, EXE download URL and SHA-256 when supplied by GitHub. Inform the user when a newer version is available, outside active recording. Does not download, install, restart or affect the game.', z.strictObject({force: z.boolean().default(true)}), true, true],
  ['pvz_get_save_info', 'get_save_info', 'Read the loaded native profile save identity, next adventure level, SHA-256 and can_edit_progress without pausing or changing the save. Use saved_level, save_sha256 and session_id as guards for pvz_admin_set_progress. Editing requires the main menu, no board/replay, and matching disk/memory progress. Only adventure progress is editable; arbitrary save fields are unsupported.', z.strictObject({ expected_session_id: session }), true, true],
  ['pvz_set_commentary', 'set_commentary', 'Publish a short spectator-facing plan after deciding, and a summary of operations performed (including multiple plants). Outlined transparent captions persist across pauses; commentary_ms limits how quickly changed text replaces the previous explanation. Publish plan and actual action together only for meaningful changes; do not refresh filler every step. Presentation I/O failure returns display_updated=false and does not fail game actions. No extra game state or wait is added. Updates HUD text only; does not pause, advance, spend sun, or change speed. Each caption displays at most two lines. Empty text clears a custom caption.', z.strictObject({ plan:z.string().max(96).optional(), action:z.string().max(160).optional(), ...mutation }).refine(value=>value.plan!==undefined || value.action!==undefined,{message:'Provide plan or action'}), false, true],
  ['pvz_launch_game', 'launch_game', 'Launch the verified local game if needed, then return status. Reuses an existing matching process. Does not require mouse or screen input.', z.strictObject({}), false, true],
  ['pvz_list_modes', 'list_modes', 'Return the backend mode catalog and support information. Catalog membership or a supported flag does not imply that a mode is unlocked or has passed a live gameplay test.', z.strictObject({}), true, true],
  ['pvz_start_game', 'start_game', 'Start through the native bridge. Omit level to use the current saved adventure progress (for example 1-4 after clearing the first 3 levels). Supplying level is an explicit jump to adventure level 1–50. mode may be adventure, a catalog key or decimal mode ID. Existing play is replaced only with force_new=true. Check the returned phase for seed selection.', z.strictObject({ mode: z.string().min(1).max(96).default('adventure'), level: z.number().int().min(1).max(50).optional(), force_new: z.boolean().default(false), ...mutation }), false, false],
  ['pvz_return_to_menu', 'return_to_menu', 'Leave the current level and return to the native main menu through the game bridge. Current unsaved level progress may be discarded.', z.strictObject(mutation), false, true],
  ['pvz_status', 'status', 'Inspect the running local game and bridge status. Does not pause or alter the game.', z.strictObject({}), true, true],
  ['pvz_get_state', 'get_state', 'Pause the current board, then return a consistent JSON snapshot with plants, zombies, projectiles, sun, cards, and board_epoch. Read this before making decisions.', z.strictObject({ expected_session_id: session }), false, true],
  ['pvz_pause', 'pause', 'Pause board simulation while keeping the game UI and control bridge responsive.', z.strictObject(mutation), false, true],
  ['pvz_advance', 'advance', 'Advance a finite game-time duration and pause. With neither duration_ms nor ticks, uses advance_ms preference (initially 2000 game ms); default speed is 1x, so this takes about 2 real seconds. 1 update=10 game ms. Supply ticks OR duration_ms to override. wait=false returns immediately with estimated_wait_ms: sleep, then read status; use get_state when phase=decision. Early get_state pauses remaining budget. No commentary-related wait is added. wait=true retains the 40-second blocking limit; native transitions can stop early.', z.strictObject({ ticks: z.number().int().min(1).max(20000).optional(), duration_ms:z.number().int().min(10).max(200000).multipleOf(10).optional(), speed, wait: z.boolean().default(true), expected_board_epoch: epoch.optional(), ...mutation }).refine(value=>value.ticks===undefined || value.duration_ms===undefined,{message:'Use ticks or duration_ms, not both'}), false, false],
  ['pvz_resume', 'resume', 'Resume continuous simulation at the selected speed. Call pvz_pause or pvz_get_state to stop it.', z.strictObject({ speed, ...mutation }), false, true],
  ['pvz_select_seeds', 'select_seeds', 'Choose an unlocked seed deck in native level preparation, then start play. Enabled for adventure (mode 0) and Zombotany / WAR_AND_PEAS (mode 16). First read available_seeds and the required deck size from current state. Supply unique plant type IDs 0–47, exactly the required count; locked seeds and Imitator are rejected. Requires the current board_epoch.', z.strictObject({ seeds: selectedSeeds, expected_board_epoch: epoch, ...mutation }), false, false],
  ['pvz_can_place', 'can_place', 'Check whether a seed card can be planted at the requested grid cell, including game legality, sun, and cooldown. Enabled for adventure (mode 0) and Zombotany / WAR_AND_PEAS (mode 16). Does not spend sun or place a plant; it may pause the board to check consistently.', z.strictObject(placement), false, true],
  ['pvz_place_plant', 'place_plant', 'Plant a current seed card through normal game rules, spending sun and starting cooldown. Enabled for adventure (mode 0) and Zombotany / WAR_AND_PEAS (mode 16). Coordinates and slots are 1-based. Requires a current board_epoch.', z.strictObject({ ...placement, command_id: commandId }), false, false],
  ['pvz_collect', 'collect', 'Pick up an existing item through the original game pickup sound and collection functions. Accepts normal sun/money items and the shovel reward. It cannot spawn items, set resources directly, or set progress. Use a current coin id from get_state.', z.strictObject({ coin_id: z.string().min(1).max(96), expected_board_epoch: epoch, ...mutation }), false, false],
  ['pvz_set_presentation', 'set_presentation', 'Set spectator preferences: speed 1–32 (default 1), show_overlay, commentary_ms (default 2000, 0–30000) for nonblocking caption visibility, and advance_ms (default 2000 game ms, multiples of 10). Speed changes preserve an active advance budget; advance_ms affects the next advance with no explicit duration. Captions remain visible throughout advancing. Legacy decision_hold_ms is an opt-in pre-advance pause and defaults to 0; keep it 0 for recording without extra waits. Empty arguments return settings and ensure the HUD runs. Does not change resources, profile or outcome.', z.strictObject({ speed, show_overlay: z.boolean().optional(), commentary_ms:z.number().int().min(0).max(30000).optional(), advance_ms:z.number().int().min(10).max(200000).multipleOf(10).optional(), decision_hold_ms: z.number().int().min(0).max(5000).optional(), ...mutation }), false, true],
  ['pvz_admin_set_sun', 'admin_set_sun', 'Administrator cheat: set stored sun directly. This changes normal game balance. Requires a current board_epoch.', z.strictObject({ amount: z.number().int().min(0).max(9990), expected_board_epoch: epoch, ...mutation }), false, true],
  ['pvz_admin_set_progress', 'admin_set_progress', 'Explicit administrator save edit: set the next adventure level in the loaded profile and its native v12 save. Requires the main menu, no board/replay, exact current level, current save SHA-256 and session identity. Creates a D-drive backup first. Does not start adventure or change sun. Use only when the user requests a persistent progress change.', z.strictObject({ level: z.number().int().min(1).max(50), expected_current_level: z.number().int().min(1).max(50), expected_save_sha256: z.string().regex(/^[a-f0-9]{64}$/), ...mutation, expected_session_id:z.string().min(1).max(128) }), false, true],
];

function createServer() {
  const server = new McpServer({ name: 'pvz-local-toolset', version: VERSION }, { capabilities: { tools: {} } });
  for (const [name, method, description, schema, readOnlyHint, idempotentHint] of specifications) {
    server.registerTool(name, {
      description, inputSchema: schema, outputSchema: z.looseObject({}),
      annotations: { readOnlyHint, destructiveHint: ['start_game', 'return_to_menu', 'admin_set_progress'].includes(method), idempotentHint, openWorldHint: method === 'check_update' },
    }, async (params, context) => {
      try {
        const result = method === 'check_update' ? await updates.check({force: params.force}) :
          await backend.request(method, params, context.mcpReq.signal);
        if (method === 'status') result.update = updates.snapshot();
        const output = result !== null && typeof result === 'object' && !Array.isArray(result) ? result : { value: result };
        return { content: [{ type: 'text', text: JSON.stringify(output) }], structuredContent: output };
      } catch (error) {
        const output = { error: { code: error.code || 'ADAPTER_ERROR', message: error.message, ...(error.details === undefined ? {} : { details: error.details }) } };
        return { isError: true, content: [{ type: 'text', text: JSON.stringify(output) }], structuredContent: output };
      }
    });
  }
  return server;
}

const handle = serveStdio(createServer, { onerror: error => console.error(`[pvz-mcp] ${error.message}`) });
updates.start();
process.stdin.once('end', () => { backend.close(); updates.close(); });
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
  backend.close();
  updates.close();
  void handle.close();
});
