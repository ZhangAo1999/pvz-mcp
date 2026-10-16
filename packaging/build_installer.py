"""Compile the offline EXE installer from source with the Windows .NET compiler."""
import argparse,json,os,subprocess
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]

def build_installer(bundle,payload_zip,output):
    bundle=Path(bundle);payload_zip=Path(payload_zip);output=Path(output)
    version=json.loads((bundle/'release.json').read_text(encoding='utf-8'))['version']
    framework=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'Microsoft.NET'/'Framework64'/'v4.0.30319'
    compiler=Path(os.environ.get('PVZ_CSC',str(framework/'csc.exe')))
    if not compiler.exists():raise ValueError('Set PVZ_CSC to the Windows .NET Framework csc.exe')
    build_root=output.parent/'installer-build';build_root.mkdir(parents=True,exist_ok=True)
    metadata=build_root/'installer-metadata.json'
    metadata.write_text(json.dumps(dict(version=version)),encoding='utf-8')
    numeric=version.split('-')[0]+'.0'
    info=build_root/'AssemblyInfo.cs'
    info.write_text('using System.Reflection;\n[assembly: AssemblyTitle("PvZ MCP Setup")]\n'
        '[assembly: AssemblyVersion("'+numeric+'")]\n'
        '[assembly: AssemblyFileVersion("'+numeric+'")]\n',encoding='utf-8')
    args=[str(compiler),'/nologo','/target:winexe','/platform:anycpu','/optimize+','/codepage:65001',
        '/out:'+str(output),'/win32manifest:'+str(SOURCE/'packaging'/'installer.manifest'),
        '/resource:'+str(payload_zip)+',Payload.zip',
        '/resource:'+str(SOURCE/'app'/'compatibility.json')+',Compatibility.json',
        '/resource:'+str(metadata)+',Metadata.json']
    for name in ['System.dll','System.Core.dll','System.Drawing.dll','System.Windows.Forms.dll',
                 'System.IO.Compression.dll','System.IO.Compression.FileSystem.dll','System.Web.Extensions.dll']:
        args.append('/reference:'+str(framework/name))
    args.extend([str(SOURCE/'packaging'/'Installer.cs'),str(info)])
    subprocess.run(args,check=True)
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('bundle');parser.add_argument('payload_zip');parser.add_argument('output')
    args=parser.parse_args();print(build_installer(args.bundle,args.payload_zip,args.output))
