import tempfile
from pathlib import Path
class Artifacts:
    def __init__(self,*args,**kwargs):
        root=Path(r'D:\Temp\Codex\待删除\PvZ-MCP-tests');root.mkdir(parents=True,exist_ok=True)
        self.name=tempfile.mkdtemp(dir=str(root))
    def __enter__(self):return self.name
    def __exit__(self,*args):return False
    def cleanup(self):pass
