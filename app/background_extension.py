"""Main-thread background control for the exact supported executable.

Only dismisses native pause dialog 19, using LawnApp::KillDialog. Other dialogs
retain native pause semantics. Installation changes the bridge, never the EXE.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import struct
from paths import DATA

KILL_DIALOG = 0x45e910


def assemble(address, mailbox, trampoline, continuation):
    code=bytearray();labels={};fix=[]
    def e(text):code.extend(bytes.fromhex(text))
    def u(value):code.extend(struct.pack('<I',value & 0xffffffff))
    def mark(name):labels[name]=len(code)
    def jump(op,target):e(op);fix.append((len(code),target));u(0)
    e('9c60')
    e('83be2009000003');jump('0f85','done')
    e('8b8668080000 85c0');jump('0f84','done')
    e('3b4710');jump('0f85','done')
    # Pause acknowledgements must also work while a native modal stops Board.
    e('8b472c 3b4730');jump('0f84','running')
    e('837f4800');jump('0f84','epoch_ok')
    e('8b4748 3b4714');jump('0f84','epoch_ok')
    e('c7473802000000');jump('e9','pause_ack')
    mark('epoch_ok')
    e('837f3401');jump('0f85','pending_run')
    e('837f4800');jump('0f84','pause_ok')
    e('8b4748 3b4714');jump('0f84','pause_ok')
    e('c7473802000000');jump('e9','pause_ack')
    mark('pause_ok')
    e('c7471c01000000 c7472000000000 c7475405000000 c7473800000000 c7474c00000000')
    mark('pause_ack');e('8b472c 894730');jump('e9','done')
    mark('pending_run')
    e('837f3402');jump('0f82','running')
    e('837f3409');jump('0f86','resume_native')
    mark('running')
    e('837f1c00');jump('0f84','resume_native')
    e('837f2000');jump('0f84','done')
    mark('resume_native')
    # KillDialog(19) is a no-op when absent. It restores widget focus and calls
    # Board::Pause(false) only when no other native modal requires a pause.
    e('6a13 8bce');jump('e8',KILL_DIALOG)
    e('8b8668080000 80b87c01000000');jump('0f84','done')
    e('8b472c 3b4730');jump('0f84','done')
    e('837f3402');jump('0f82','done')
    e('837f3409');jump('0f87','done')
    mark('menu_blocked');e('c7473817000000');jump('e9','pause_ack')
    mark('done');e('619d')
    # Replay the complete replaced App update call. Continue at its popad.
    e('60 8bce');jump('e8',trampoline);jump('e9',continuation)
    for pos,target in fix:
        dest=address+labels[target] if isinstance(target,str) else target
        struct.pack_into('<i',code,pos,dest-address-pos-4)
    return bytes(code)


def record_path(ctl):
    return DATA/'extensions'/(ctl.session.replace(':','-')+'-background.json')


def site(ctl, base):
    at=ctl.manifest['labels']['app_update']-ctl.manifest['code_va']
    original=base[at:at+8]
    if original[:4]!=bytes.fromhex('608bcee8'):
        raise ValueError('Unknown native App update call')
    target=ctl.manifest['code_va']+at+8+struct.unpack_from('<i',original,4)[0]
    if target!=ctl.manifest['labels']['app_trampoline']:
        raise ValueError('Native App trampoline differs')
    return at,original


def unwrap(ctl, base, actual):
    at,original=site(ctl,base)
    ctl.background_control=False
    if actual[at:at+8]==original:return actual
    r=json.loads(record_path(ctl).read_text(encoding='utf-8'))
    if r.get('version')!=1 or r['session_id']!=ctl.session or r['build']!=ctl.manifest['sha256']:
        raise ValueError('Background extension identity differs')
    stub=assemble(r['address'],ctl.base,ctl.manifest['labels']['app_trampoline'],ctl.manifest['code_va']+at+8)
    patch=b'\xe9'+struct.pack('<i',r['address']-(ctl.manifest['code_va']+at+5))+b'\x90'*3
    if actual[at:at+8]!=patch or ctl.mem.read(r['address'],len(stub))!=stub:
        raise ValueError('Background extension differs')
    normalized=bytearray(actual);normalized[at:at+8]=original
    ctl.background_control=True
    return bytes(normalized)


def install(ctl):
    from attach import api, frozen
    from collect_extension import verify
    base=ctl.bridge_code
    actual=ctl.mem.read(ctl.manifest['code_va'],len(base))
    verify(ctl,base,actual)
    if ctl.background_control:return
    pending=ctl.mailbox()
    if pending['request']!=pending['ack'] and pending['command']!=1:
        raise ValueError('A previous game command is pending; inspect its outcome before enabling background control')
    if ctl.mem.read(KILL_DIALOG,13)!=bytes.fromhex('8b44240453508bd9e853bd1b00'):
        raise ValueError('Native KillDialog signature differs')
    at,original=site(ctl,base);target=ctl.manifest['code_va']+at
    k=api();address=k.VirtualAllocEx(ctl.handle,None,4096,0x3000,0x04)
    if not address or not 0x10000<=address<0x7fff0000:raise ValueError('Cannot allocate background extension')
    committed=False
    try:
        stub=assemble(address,ctl.base,ctl.manifest['labels']['app_trampoline'],target+8)
        ctl.mem.write(address,stub)
        old=W.DWORD()
        if not k.VirtualProtectEx(ctl.handle,address,4096,0x20,C.byref(old)):raise C.WinError(C.get_last_error())
        if not k.FlushInstructionCache(ctl.handle,address,len(stub)):raise C.WinError(C.get_last_error())
        patch=b'\xe9'+struct.pack('<i',address-target-5)+b'\x90'*3
        with frozen(k,ctl.pid,[(target,target+8)]):
            current=ctl.mem.read(ctl.manifest['code_va'],len(base))
            verify(ctl,base,current)
            if ctl.mem.read(target,8)!=original:raise ValueError('App call changed before installation')
            record=dict(version=1,session_id=ctl.session,build=ctl.manifest['sha256'],address=address)
            from presentation import write_json
            write_json(record_path(ctl),record)
            if not k.VirtualProtectEx(ctl.handle,target,8,0x40,C.byref(old)):raise C.WinError(C.get_last_error())
            try:
                ctl.mem.write(target,patch)
                verify(ctl,base,ctl.mem.read(ctl.manifest['code_va'],len(base)))
                committed=True
            except Exception:
                ctl.mem.write(target,original)
                raise
            finally:
                restored=W.DWORD();k.VirtualProtectEx(ctl.handle,target,8,old.value,C.byref(restored))
                k.FlushInstructionCache(ctl.handle,target,8)
    finally:
        if not committed:k.VirtualFreeEx(ctl.handle,address,0,0x8000)
