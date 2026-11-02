"""Attach a verified bridge to an existing exact-build process.

No remote thread or game/save file writes. All target threads are briefly
suspended and checked outside hook instructions before any detour is committed.
Every successful SuspendThread is balanced even if validation or writing fails.
"""
import contextlib
import ctypes as C
from ctypes import wintypes as W
import json
import struct
import time
from bridge_asm import assemble
from runtime_bridge import inspect_game,sun_stub
from paths import DATA

class THREADENTRY32(C.Structure):
    _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ThreadID',W.DWORD),
              ('th32OwnerProcessID',W.DWORD),('tpBasePri',W.LONG),('tpDeltaPri',W.LONG),('dwFlags',W.DWORD)]

def api():
    k=C.WinDLL('kernel32',use_last_error=True)
    specs={
        'CreateToolhelp32Snapshot':([W.DWORD,W.DWORD],W.HANDLE),
        'Thread32First':([W.HANDLE,C.POINTER(THREADENTRY32)],W.BOOL),
        'Thread32Next':([W.HANDLE,C.POINTER(THREADENTRY32)],W.BOOL),
        'OpenThread':([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
        'SuspendThread':([W.HANDLE],W.DWORD),'ResumeThread':([W.HANDLE],W.DWORD),
        'Wow64GetThreadContext':([W.HANDLE,W.LPVOID],W.BOOL),
        'GetExitCodeThread':([W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),
        'VirtualAllocEx':([W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD,W.DWORD],W.LPVOID),
        'VirtualFreeEx':([W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD],W.BOOL),
        'VirtualProtectEx':([W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),
        'FlushInstructionCache':([W.HANDLE,W.LPCVOID,C.c_size_t],W.BOOL),
        'CloseHandle':([W.HANDLE],W.BOOL)}
    for name,(args,result) in specs.items():
        fn=getattr(k,name);fn.argtypes=args;fn.restype=result
    return k

def thread_ids(k,pid):
    snap=k.CreateToolhelp32Snapshot(4,0)
    if snap==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
    try:
        item=THREADENTRY32();item.dwSize=C.sizeof(item);result=[]
        ok=k.Thread32First(snap,C.byref(item))
        while ok:
            if item.th32OwnerProcessID==pid:result.append(item.th32ThreadID)
            item.dwSize=C.sizeof(item);ok=k.Thread32Next(snap,C.byref(item))
        if not result:raise ValueError('Target process has no live threads')
        return result
    finally:k.CloseHandle(snap)

def release_threads(k,held):
    errors=[]
    for tid,h in reversed(held):
        if k.ResumeThread(h)==0xffffffff:errors.append(tid)
        k.CloseHandle(h)
    if errors:raise RuntimeError('Could not balance suspended target threads: '+str(errors))

@contextlib.contextmanager
def frozen(k,pid,hook_ranges):
    for attempt in range(30):
        held=[];ready=False
        try:
            for tid in thread_ids(k,pid):
                h=k.OpenThread(0x4a,False,tid)
                if not h:
                    if C.get_last_error()==87:continue # exited since enumeration
                    raise C.WinError(C.get_last_error())
                if k.SuspendThread(h)==0xffffffff:
                    k.CloseHandle(h);raise C.WinError(C.get_last_error())
                held.append((tid,h))
            ready=set(thread_ids(k,pid)).issubset({tid for tid,_ in held})
            for tid,h in held:
                # WOW64_CONTEXT: CONTEXT_CONTROL, EIP at 184, 716 bytes total.
                context=C.create_string_buffer(716);struct.pack_into('<I',context,0,0x10001)
                if not k.Wow64GetThreadContext(h,C.byref(context)):
                    exit_code=W.DWORD()
                    if k.GetExitCodeThread(h,C.byref(exit_code)) and exit_code.value!=259:continue
                    raise C.WinError(C.get_last_error())
                ip=struct.unpack_from('<I',context,184)[0]
                if any(start<=ip<end for start,end in hook_ranges):ready=False
            if ready:
                yield
                return
        finally:release_threads(k,held)
        time.sleep(.01)
    raise ValueError('Target threads remained inside hook instructions; no game hooks changed')

def record_path(session):
    return DATA/'runtime'/'bridges'/(session.replace(':','-')+'.json')

def locate_bridge(ctl):
    """Locate our known relocated bridge from its App detour, never by PID alone.

    The caller still verifies every bridge byte before using this candidate.
    This allows separate installations to reuse an unextended bridge too.
    """
    hook=ctl.mem.read(0x45f8c0,6)
    if hook[:1]!=b'\xe9' or hook[5:]!=b'\x90':return None
    layout=assemble()[2]
    entry=0x45f8c5+struct.unpack_from('<i',hook,1)[0]
    base=entry-(layout['labels']['app_entry']-layout['code_va'])
    if not 0x10000<=base<0x7fff0000 or base%4096:return None
    try:
        if ctl.mem.read(base+4096,8)!=b'PZCT'+struct.pack('<I',layout['version']):return None
    except Exception:return None
    return dict(ctl.manifest,**assemble(code_address=base,data_address=base+4096)[2])

def attach(ctl):
    checked=inspect_game(ctl.path)
    code,_,layout,hooks=assemble()
    k=api();address=None;committed=[];started=time.monotonic()
    ranges=[(target,target+len(old)) for target,old,_ in hooks]+[(0x43cc10,0x43cc16)]
    def check(result):
        if not result:raise C.WinError(C.get_last_error())
    def write(address,data,executable=False):
        old=W.DWORD()
        if executable:check(k.VirtualProtectEx(ctl.handle,address,len(data),0x40,C.byref(old)))
        try:
            ctl.mem.write(address,data)
            if ctl.mem.read(address,len(data))!=data:raise ValueError('Runtime write verification failed')
        finally:
            if executable:
                previous=W.DWORD();check(k.VirtualProtectEx(ctl.handle,address,len(data),old.value,C.byref(previous)))
        check(k.FlushInstructionCache(ctl.handle,address,len(data)))
    try:
        with frozen(k,ctl.pid,ranges):
            # Fail closed on other trainers, stale builds and already-hooked games.
            for target,old,_ in hooks:
                if ctl.mem.read(target,len(old))!=old:raise ValueError('Original hook differs at '+hex(target))
            if checked.get('auto_sun',True) and ctl.mem.read(0x43cc10,6)!=bytes.fromhex('83ec20538bd8'):
                raise ValueError('Sun update already modified by another tool')
            app=ctl.mem.u32(0x755e0c)
            board=ctl.mem.u32(app+0x868) if app else 0
            before=ctl.mem.read(board,0x5700) if board else None
            address=k.VirtualAllocEx(ctl.handle,layout['code_va'],8192,0x3000,0x04)
            if address!=layout['code_va']:
                if address:check(k.VirtualFreeEx(ctl.handle,address,0,0x8000));address=None
                address=k.VirtualAllocEx(ctl.handle,None,8192,0x3000,0x04)
            if not address or not 0x10000<=address<0x7fff0000:raise ValueError('No bounded x86 bridge allocation')
            code,mailbox,layout,hooks=assemble(code_address=address,data_address=address+4096)
            mailbox=bytearray(mailbox)
            initial=dict(board=board,epoch=1,paused=1,speed=1,stop=5,auto_pause_play=1,
                         app=app,scene=ctl.mem.i32(app+0x920) if app else 0)
            for name,value in initial.items():struct.pack_into('<I',mailbox,layout['mailbox'][name],value)
            write(address,code);write(address+4096,bytes(mailbox))
            stub=sun_stub(address+0xe00);write(address+0xe00,stub)
            protection=W.DWORD();check(k.VirtualProtectEx(ctl.handle,address,4096,0x20,C.byref(protection)))
            patches=[(target,old,b'\xe9'+struct.pack('<i',layout['labels'][label]-target-5)+b'\x90'*(len(old)-5)) for target,old,label in hooks]
            patches.append((0x43cc10,bytes.fromhex('83ec20538bd8'),b'\xe9'+struct.pack('<i',address+0xe00-0x43cc10-5)+b'\x90'))
            try:
                for target,old,patch in patches:
                    committed.append((target,old));write(target,patch,True)
                if board and ctl.mem.read(board,0x5700)!=before:raise ValueError('Native board changed during bridge installation')
                manifest=dict(checked,**layout)
                record=dict(session_id=ctl.session,pid=ctl.pid,manifest=manifest,
                            board_preserved=bool(board),native_board_bytes_unchanged=True,
                            thread_suspension_balanced=True,attach_seconds=time.monotonic()-started)
                path=record_path(ctl.session);path.parent.mkdir(parents=True,exist_ok=True)
                staged=path.with_suffix('.new');staged.write_text(json.dumps(record,indent=2),encoding='utf-8');staged.replace(path)
            except Exception:
                for target,old in reversed(committed):write(target,old,True)
                committed=[]
                raise
        return record
    except Exception:
        if address and not committed:k.VirtualFreeEx(ctl.handle,address,0,0x8000)
        raise
