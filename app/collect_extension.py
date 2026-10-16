"""Optional native pickup command, installed without restarting a paused board.

Only the idle, previously unknown-command fallback is redirected. Existing
commands are unchanged. The new routine runs on the normal App update thread,
validates the Board/generation handle, then uses the original pickup functions.
No resource, progression, end-level or cooldown field is written by this code.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib,json,struct
from pathlib import Path
from paths import DATA

def assemble(address,mailbox,ack):
    code=bytearray();labels={};fix=[]
    def e(h):code.extend(bytes.fromhex(h))
    def u(n):code.extend(struct.pack('<I',n&0xffffffff))
    def mark(s):labels[s]=len(code)
    def j(op,target):e(op);fix.append((len(code),target));u(0)
    def result(n):e('c74738');u(n);j('e9',ack)
    e('817f340e000000');j('0f85','unknown')
    e('83be2009000003');j('0f85','not_playing')
    e('8b8668080000 85c0');j('0f84','stale')
    e('3b4710');j('0f85','stale')
    e('83bf1c00000001');j('0f85','pause_required')
    e('80b87c01000000');j('0f85','not_playing') # native pause/menu is active
    e('8b5f3c 0fb7d3') # ebx=complete handle, edx=index
    e('3b9000010000');j('0f83','stale')
    e('81b80401000000100000');j('0f87','stale') # capacity <=4096
    e('69d2d8000000 0390fc000000') # pointer = pool + index*0xd8
    e('399ad0000000');j('0f85','stale')
    e('394204');j('0f85','stale')
    e('807a3800');j('0f85','stale')
    e('807a5000');j('0f85','already')
    e('837a3c00');j('0f85','unsupported')
    e('8b4258 83f809');j('0f84','pickup')
    e('83f801');j('0f82','unsupported');e('83f806');j('0f87','unsupported')
    mark('pickup');e('60');j('e8',0x43e270)
    e('8b4c2414');j('e8',0x43d740);e('61') # saved EDX is the Coin pointer
    e('c7474c01000000');result(0)
    mark('already');e('c7474c00000000');result(0)
    mark('stale');result(20)
    mark('unsupported');result(21)
    mark('not_playing');result(4)
    mark('pause_required');result(3)
    mark('unknown');result(1)
    for pos,target in fix:
        dest=address+labels[target] if isinstance(target,str) else target
        struct.pack_into('<i',code,pos,dest-address-pos-4)
    return bytes(code)

def record_path(ctl):
    return DATA/'extensions'/(ctl.session.replace(':','-')+'-'+ctl.manifest['sha256'][:16]+'.json')

def fallback(ctl,base):
    needle=bytes.fromhex('817f340d0000000f84')
    positions=[i for i in range(len(base))if base.startswith(needle,i)]
    if len(positions)!=1:raise ValueError('Unknown app dispatch layout')
    at=positions[0]+13
    original=base[at:at+12]
    if original[:7]!=bytes.fromhex('c7473801000000') or original[7]!=0xe9:raise ValueError('Fallback signature differs')
    assert ctl.manifest['code_va']+at+12+struct.unpack_from('<i',original,8)[0]==ctl.manifest['labels']['app_ack']
    return at,original

def verify(ctl,base,actual):
    if actual==base:return False
    file=record_path(ctl)
    if not file.exists():raise ValueError('Unrecognized live bridge code')
    r=json.loads(file.read_text(encoding='utf-8'))
    at,old=fallback(ctl,base)
    if r['session_id']!=ctl.session or r['build']!=ctl.manifest['sha256'] or r['patch_offset']!=at:raise ValueError('Live extension identity differs')
    stub=assemble(r['address'],ctl.base,ctl.manifest['labels']['app_ack'])
    patch=b'\xe9'+struct.pack('<i',r['address']-(ctl.manifest['code_va']+at+5))+b'\x90'*7
    expected=bytearray(base);expected[at:at+12]=patch
    if actual!=expected or ctl.mem.read(r['address'],len(stub))!=stub:raise ValueError('Live pickup extension differs')
    return True

def install(ctl):
    from bridge_asm import assemble as assemble_bridge
    base=assemble_bridge()[0]
    actual=ctl.mem.read(ctl.manifest['code_va'],len(base))
    if verify(ctl,base,actual):return
    mb=ctl.mailbox()
    if mb['paused']!=1 or mb['remaining'] or mb['request']!=mb['ack'] or mb['busy']:
        raise ValueError('Pickup installation requires a paused board and idle mailbox')
    at,original=fallback(ctl,base)
    k=C.WinDLL('kernel32',use_last_error=True)
    k.VirtualAllocEx.argtypes=[W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD,W.DWORD];k.VirtualAllocEx.restype=W.LPVOID
    k.VirtualProtectEx.argtypes=[W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD,C.POINTER(W.DWORD)];k.VirtualProtectEx.restype=W.BOOL
    k.FlushInstructionCache.argtypes=[W.HANDLE,W.LPCVOID,C.c_size_t];k.FlushInstructionCache.restype=W.BOOL
    address=k.VirtualAllocEx(ctl.handle,None,4096,0x3000,0x04)
    if not address or not 0x10000<=address<0x7fff0000:raise ValueError('Cannot allocate bounded x86 extension')
    stub=assemble(address,ctl.base,ctl.manifest['labels']['app_ack']);ctl.mem.write(address,stub)
    old=W.DWORD()
    if not k.VirtualProtectEx(ctl.handle,address,4096,0x20,C.byref(old)):raise OSError(C.get_last_error(),'Cannot protect pickup code')
    if not k.FlushInstructionCache(ctl.handle,address,len(stub)):raise OSError(C.get_last_error(),'Cannot flush pickup code')
    patch=b'\xe9'+struct.pack('<i',address-(ctl.manifest['code_va']+at+5))+b'\x90'*7
    r=dict(session_id=ctl.session,build=ctl.manifest['sha256'],address=address,patch_offset=at,
        original=original.hex(),replacement=patch.hex(),code=stub.hex(),sha256=hashlib.sha256(stub).hexdigest(),
        writes='RX extension and idle unknown-command branch only; no game resource/progress fields')
    file=record_path(ctl);file.parent.mkdir(parents=True,exist_ok=True)
    staged=file.with_suffix('.staged');staged.write_text(json.dumps(r,indent=2),encoding='utf-8');staged.replace(file)
    target=ctl.manifest['code_va']+at
    if not k.VirtualProtectEx(ctl.handle,target,len(patch),0x40,C.byref(old)):raise OSError(C.get_last_error(),'Cannot update idle dispatch')
    try:ctl.mem.write(target,patch)
    finally:
        previous=W.DWORD();k.VirtualProtectEx(ctl.handle,target,len(patch),old.value,C.byref(previous))
        k.FlushInstructionCache(ctl.handle,target,len(patch))
    assert verify(ctl,base,ctl.mem.read(ctl.manifest['code_va'],len(base)))
