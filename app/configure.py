"""Configure a user-supplied game without writing the game or its saves."""
import argparse,json,os,time,uuid,shutil
from pathlib import Path
from paths import DATA
from runtime_bridge import inspect_game

def write_json(path,value):
    staged=path.with_suffix('.new')
    staged.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(str(staged),str(path))

def configure(game,allow_unsupported=False):
    try:manifest=inspect_game(game)
    except ValueError:
        if not allow_unsupported:raise
        # A selected unsupported game must not silently leave a previous game active.
        target=DATA/'bridge-manifest.json'
        backup=None
        if target.exists():
            directory=DATA/'backup'/('configuration-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
            directory.mkdir(parents=True)
            backup=directory/target.name
            shutil.move(str(target),str(backup))
        result=dict(configured=False,reason='UNSUPPORTED_GAME',requested_game=str(Path(game).resolve()),
            message='Tools installed; game control disabled until a supported EXE is selected.',
            previous_configuration_backup=str(backup) if backup else None)
        write_json(DATA/'game-configuration.json',result)
        return result
    target=DATA/'bridge-manifest.json';staged=target.with_suffix('.new')
    write_json(target,manifest)
    result=dict(configured=True,game=manifest['game'],sha256=manifest['sha256'],data=str(DATA))
    write_json(DATA/'game-configuration.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('game');parser.add_argument('--allow-unsupported',action='store_true');args=parser.parse_args()
    print(json.dumps(configure(args.game,args.allow_unsupported),ensure_ascii=False))
