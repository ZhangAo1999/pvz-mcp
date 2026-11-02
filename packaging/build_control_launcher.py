"""Compile the GUI launcher as a release artifact, never as a source binary."""
import os
import subprocess
from pathlib import Path


def build_control_launcher(output):
    source = Path(__file__).resolve().parent
    compiler = Path(os.environ.get('PVZ_CSC', str(Path(os.environ['SystemRoot']) /
                    'Microsoft.NET/Framework64/v4.0.30319/csc.exe')))
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:anycpu',
                    '/optimize+', '/codepage:65001', '/out:' + str(output),
                    '/reference:System.dll', '/reference:System.Web.Extensions.dll',
                    '/reference:System.Windows.Forms.dll', str(source / 'ControlLauncher.cs')], check=True)
