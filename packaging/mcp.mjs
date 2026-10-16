// Stable stdio entry. A version switch takes effect on the next MCP connection.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
const root=path.dirname(fileURLToPath(import.meta.url));
const active=JSON.parse(fs.readFileSync(path.join(root,'active.json'),'utf8'));
if(!/^\d+\.\d+\.\d+(?:-[a-z0-9.]+)?$/.test(active.version))throw Error('Invalid active version');
const release=path.join(root,'versions',active.version);
const runtime=path.join(release,'runtime');
const env={...process.env,PVZ_DATA_DIR:path.join(root,'data'),
 PVZ_PYTHON:path.join(runtime,'python','python.exe'),
 PVZ_SDK_ROOT:path.join(release,'node_modules'),PVZ_TEMP:path.join(root,'data','temp'),
 PYTHONPYCACHEPREFIX:path.join(root,'data','cache','python'),NODE_COMPILE_CACHE:path.join(root,'data','cache','node')};
const command=process.argv[2]==='--smoke'?'smoke-mcp.mjs':'mcp-server.mjs';
const child=spawn(path.join(runtime,'node','node.exe'),[path.join(release,'app',command)],
 {cwd:release,env,stdio:'inherit',windowsHide:true});
child.on('error',e=>{process.stderr.write(e.message+'\n');process.exitCode=1;});
child.on('exit',code=>{process.exitCode=code??1;});
