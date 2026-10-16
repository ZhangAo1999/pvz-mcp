"""Build offline installer and clean source archives. Never includes any game file."""
import argparse,hashlib,json,os,shutil,sys,zipfile
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]
VERSION=json.loads((SOURCE/'package.json').read_text(encoding='utf-8'))['version']
SDK=Path(os.environ.get('PVZ_BUILD_SDK',str(SOURCE/'node_modules')))
PYTHON=Path(os.environ.get('PVZ_BUILD_PYTHON_DIR',sys.base_prefix))
NODE=Path(os.environ.get('PVZ_BUILD_NODE_DIR',str(Path(shutil.which('node.exe') or 'node.exe').resolve().parent)))

def ignore(path,names):
    return [n for n in names if n in ['__pycache__','site-packages','.git','.bin','test','tests','Doc','include','libs','Tools','Scripts','share','ensurepip'] or n.endswith('.pyc')]

def copy_dependencies(target):
    seen={};pending=['@modelcontextprotocol/server','@modelcontextprotocol/client','zod']
    while pending:
        name=pending.pop()
        if name in seen:continue
        source=SDK/name
        metadata=json.loads((source/'package.json').read_text(encoding='utf-8'))
        # This local lock set is hoisted. Preserve exact versions; never resolve a new range here.
        seen[name]=dict(version=metadata['version'],license=metadata.get('license'))
        shutil.copytree(str(source),str(target/name),ignore=ignore)
        pending.extend(metadata.get('dependencies',{}))
    return seen

def archive(directory,destination):
    with zipfile.ZipFile(str(destination),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(directory.rglob('*')):
            if p.is_file():z.write(str(p),str(p.relative_to(directory)))

def build(destination):
    destination=Path(destination).resolve()
    if SOURCE in destination.parents and not (SOURCE/'dist'==destination or SOURCE/'dist' in destination.parents):
        raise ValueError('Build outside the source tree or use its dist directory')
    destination.mkdir(parents=True,exist_ok=True)
    bundle=destination/('PvZ-MCP-'+VERSION+'-windows-x64')
    if bundle.exists():raise ValueError('Build folder exists; use a new output directory or version')
    payload=bundle/'payload';payload.mkdir(parents=True)
    shutil.copy2(str(SOURCE/'package.json'),str(payload/'package.json'))
    for name in ['LICENSE','NOTICE','THIRD-PARTY-NOTICES.txt']:
        shutil.copy2(str(SOURCE/name),str(payload/name))
    shutil.copytree(str(SOURCE/'app'),str(payload/'app'),ignore=ignore)
    py=payload/'runtime'/'python';py.mkdir(parents=True)
    for name in ['python.exe','pythonw.exe','python3.dll','python38.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt']:
        shutil.copy2(str(PYTHON/name),str(py/name))
    for name in ['DLLs','Lib','tcl']:shutil.copytree(str(PYTHON/name),str(py/name),ignore=ignore)
    node=payload/'runtime'/'node';node.mkdir(parents=True)
    for name in ['node.exe','LICENSE']:shutil.copy2(str(NODE/name),str(node/name))
    dependencies=copy_dependencies(payload/'node_modules')
    (payload/'dependency-versions.json').write_text(json.dumps(dependencies,indent=2),encoding='utf-8')
    for name in ['Install.cmd','Install.ps1','mcp.mjs','Manage.ps1']:
        shutil.copy2(str(SOURCE/'packaging'/name),str(bundle/name))
    for name in ['README.md','README.txt','LICENSE','NOTICE','THIRD-PARTY-NOTICES.txt']:
        shutil.copy2(str(SOURCE/name),str(bundle/name))
    if (SOURCE/'docs').exists():
        shutil.copytree(str(SOURCE/'docs'),str(bundle/'docs'),ignore=ignore)
    hashes={p.relative_to(payload).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(payload.rglob('*')) if p.is_file()}
    release=dict(version=VERSION,channel='preview' if '-' in VERSION else 'stable',platform='windows-x64',adapter='original-1.2.0.1073-runtime-v1',
        game_included=False,files=hashes)
    (bundle/'release.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    payload_zip=destination/(bundle.name+'.zip')
    archive(bundle,payload_zip)
    from build_installer import build_installer
    installer=build_installer(bundle,payload_zip,destination/('PvZ-MCP-'+VERSION+'-Setup.exe'))
    source_zip=destination/('PvZ-MCP-'+VERSION+'-source.zip')
    with zipfile.ZipFile(str(source_zip),'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(SOURCE.rglob('*')):
            relative=p.relative_to(SOURCE)
            excluded={'.git','__pycache__','node_modules','data','dist','runtime','backup','runs','extensions'}
            if (p.is_file() and not any(part in excluded for part in relative.parts)
                    and p.suffix not in ['.pyc','.zip','.log','.exe','.dll','.pdb']
                    and not p.name.startswith('.env')):
                z.write(str(p),str(p.relative_to(SOURCE)))
    checksums=[]
    for p in sorted([*destination.glob('*.zip'),*destination.glob('*.exe')]):checksums.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name)
    (destination/'SHA256SUMS.txt').write_text('\n'.join(checksums)+'\n',encoding='utf-8')
    print(json.dumps(dict(bundle=str(bundle),source=str(source_zip),installer=str(installer),files=len(hashes),dependencies=dependencies),ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args();build(args.destination)
