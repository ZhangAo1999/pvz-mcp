"""Native adventure context for explicit replay, with retained progress receipts.

PvZ uses PlayerInfo.level as the current adventure context in many predicates,
not just Board.level (for example bowling and the Dave intro). Changing only
Board.level produces a mixed level. No resources or victory fields are edited.
"""
import hashlib, json, shutil, time
from pathlib import Path
import presentation as ui

def file(ctl):return ui.RUNTIME/('selection-'+ctl.session.replace(':','-')+'.json')
def current(ctl):
    value=ui.read_json(file(ctl))
    return value if value.get('session_id')==ctl.session and value.get('active') else None

def begin(ctl,level):
    if not level:return
    mb=ctl.mailbox();app=mb['app'];profile=ctl.mem.u32(app+0x950)
    if not profile:raise ValueError('Explicit adventure selection needs a native profile')
    original=ctl.mem.i32(profile+0x4c)
    if original==level:return
    receipt=current(ctl)
    if receipt:raise ValueError('Leave the previous replay before selecting another level')
    backup=ui.ROOT/'backup'/('selection-'+str(int(time.time()*1000)))
    backup.mkdir(parents=True,exist_ok=True);sources=[]
    for source in Path(r'C:\ProgramData\PopCap Games\PlantsVsZombies\userdata').glob('*.dat'):
        target=backup/source.name;shutil.copy2(str(source),str(target))
        sources.append(dict(path=str(source),backup=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    receipt=dict(session_id=ctl.session,profile=profile,original_level=original,selected_level=level,
        active=True,time=time.time(),backups=sources,
        reason='Explicit replay requires native PlayerInfo and Board to agree; retained progress restored on leaving replay.')
    ui.write_json(file(ctl),receipt) # write recovery receipt before native context
    ctl.mem.put(profile+0x4c,level)

def restore(ctl):
    receipt=current(ctl)
    if not receipt:return
    app=ctl.mailbox()['app'];profile=ctl.mem.u32(app+0x950)
    if profile!=receipt['profile']:raise ValueError('Replay profile changed; cannot restore another profile')
    value=ctl.mem.i32(profile+0x4c)
    retained=max(value,receipt['original_level'])
    if retained!=value:ctl.mem.put(profile+0x4c,retained)
    receipt.update(active=False,restored_at=time.time(),level_on_exit=value,retained_level=retained)
    ui.write_json(file(ctl),receipt)

def finish_if_left(ctl):
    if not current(ctl):return
    app=ctl.mailbox()['app']
    if not ctl.mem.u32(app+0x868) and ctl.mem.i32(app+0x920) in [1,4,5,6,7]:restore(ctl)
