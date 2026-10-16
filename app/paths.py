import os
from pathlib import Path
APP=Path(__file__).resolve().parent
DATA=Path(os.environ.get('PVZ_DATA_DIR',str(APP/'data'))).resolve()
DATA.mkdir(parents=True,exist_ok=True)
for name in ['logs','runtime','backup','extensions']:(DATA/name).mkdir(exist_ok=True)
