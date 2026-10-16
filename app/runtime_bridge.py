"""Launch an exact supported original EXE with a process-local bridge.

Only a newly created, suspended process is patched, before its first instruction.
Never patches an existing running game, writes an EXE, or bypasses DRM.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib, json, os, struct, subprocess
from pathlib import Path
from bridge_asm import assemble
from paths import DATA

ORIGINAL_SHA256='e1511201ec2ae9a7e30259f04bc2ec7272badb2574a2e9b405d0e916ae5c082c'

def inspect_game(path):
    path=Path(path).resolve()
    data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    if digest!=ORIGINAL_SHA256:
        raise ValueError('Unsupported game SHA-256: '+digest+'. No patch or download was performed.')
    pe=struct.unpack_from('<I',data,0x3c)[0]
    if data[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',data,pe+4)[0]!=0x14c:
        raise ValueError('Expected the supported x86 PE')
    if struct.unpack_from('<I',data,pe+24+28)[0]!=0x400000:
        raise ValueError('Unexpected image base')
    code,mailbox,manifest,hooks=assemble()
    return dict(manifest,game=str(path),sha256=digest,adapter='original-1.2.0.1073-runtime-v1',
        original_unchanged=True,auto_sun=True,bungee_hp_modified=False)

class STARTUPINFO(C.Structure):
    _fields_=[('cb',W.DWORD),('lpReserved',W.LPWSTR),('lpDesktop',W.LPWSTR),
        ('lpTitle',W.LPWSTR),('dwX',W.DWORD),('dwY',W.DWORD),('dwXSize',W.DWORD),
        ('dwYSize',W.DWORD),('dwXCountChars',W.DWORD),('dwYCountChars',W.DWORD),
        ('dwFillAttribute',W.DWORD),('dwFlags',W.DWORD),('wShowWindow',W.WORD),
        ('cbReserved2',W.WORD),('lpReserved2',C.POINTER(C.c_byte)),
        ('hStdInput',W.HANDLE),('hStdOutput',W.HANDLE),('hStdError',W.HANDLE)]
class PROCESSINFO(C.Structure):
    _fields_=[('hProcess',W.HANDLE),('hThread',W.HANDLE),('pid',W.DWORD),('tid',W.DWORD)]

def launch(path,manifest,verify_only=False):
    checked=inspect_game(path)
    if manifest['sha256']!=checked['sha256'] or manifest['code_va']!=checked['code_va']:
        raise ValueError('Installed adapter configuration differs; run Configure again')
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateProcessW.argtypes=[W.LPCWSTR,W.LPWSTR,W.LPVOID,W.LPVOID,W.BOOL,W.DWORD,
        W.LPVOID,W.LPCWSTR,C.POINTER(STARTUPINFO),C.POINTER(PROCESSINFO)]
    k.CreateProcessW.restype=W.BOOL
    k.VirtualAllocEx.argtypes=[W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD,W.DWORD];k.VirtualAllocEx.restype=W.LPVOID
    k.VirtualProtectEx.argtypes=[W.HANDLE,W.LPVOID,C.c_size_t,W.DWORD,C.POINTER(W.DWORD)];k.VirtualProtectEx.restype=W.BOOL
    k.ReadProcessMemory.argtypes=[W.HANDLE,W.LPCVOID,W.LPVOID,C.c_size_t,C.POINTER(C.c_size_t)];k.ReadProcessMemory.restype=W.BOOL
    k.WriteProcessMemory.argtypes=[W.HANDLE,W.LPVOID,W.LPCVOID,C.c_size_t,C.POINTER(C.c_size_t)];k.WriteProcessMemory.restype=W.BOOL
    k.FlushInstructionCache.argtypes=[W.HANDLE,W.LPCVOID,C.c_size_t];k.FlushInstructionCache.restype=W.BOOL
    k.ResumeThread.argtypes=[W.HANDLE];k.ResumeThread.restype=W.DWORD
    k.TerminateProcess.argtypes=[W.HANDLE,W.UINT];k.TerminateProcess.restype=W.BOOL
    k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
    pi=PROCESSINFO();si=STARTUPINFO();si.cb=C.sizeof(si)
    command=C.create_unicode_buffer(subprocess.list2cmdline([str(path)]))
    if not k.CreateProcessW(str(path),command,None,None,False,4,None,str(Path(path).parent),C.byref(si),C.byref(pi)):
        raise OSError(C.get_last_error(),'Cannot launch the supplied game')
    resumed=False
    def check(result,what):
        if not result:raise OSError(C.get_last_error(),what)
    def read(address,size):
        buf=C.create_string_buffer(size);n=C.c_size_t()
        check(k.ReadProcessMemory(pi.hProcess,address,buf,size,C.byref(n)),'Read suspended game')
        if n.value!=size:raise ValueError('Partial process read')
        return buf.raw
    def write(address,data,executable=False):
        old=W.DWORD();n=C.c_size_t()
        if executable:check(k.VirtualProtectEx(pi.hProcess,address,len(data),0x40,C.byref(old)),'Unprotect hook')
        try:
            check(k.WriteProcessMemory(pi.hProcess,address,data,len(data),C.byref(n)),'Write runtime bridge')
            if n.value!=len(data):raise ValueError('Partial process write')
            if read(address,len(data))!=data:raise ValueError('Runtime write verification failed')
        finally:
            if executable:
                previous=W.DWORD();check(k.VirtualProtectEx(pi.hProcess,address,len(data),old.value,C.byref(previous)),'Restore hook protection')
        check(k.FlushInstructionCache(pi.hProcess,address,len(data)),'Flush runtime instruction cache')
    try:
        code,mailbox,assembled,hooks=assemble()
        # Fixed address belongs to the audited x86 bridge ABI; fail instead of relocating blindly.
        address=k.VirtualAllocEx(pi.hProcess,assembled['code_va'],8192,0x3000,0x04)
        if address!=assembled['code_va']:raise ValueError('Runtime bridge address is unavailable')
        for target,expected,label in hooks:
            if read(target,len(expected))!=expected:raise ValueError('Original hook signature differs: '+hex(target))
        write(address,code);write(assembled['data_va'],mailbox)
        if manifest.get('auto_sun',True):
            target=0x43cc10;expected=bytes.fromhex('83ec20538bd8');stub_address=address+0xc00
            if read(target,6)!=expected:raise ValueError('Sun update signature differs')
            # Same native sound/collection sequence, recalculated for this process allocation.
            stub=bytearray.fromhex('9c60837858040f8247000000837858060f873d000000807838000f8533000000807850000f852900000083783c000f851f0000008b1083ba20090000030f85100000008bd0e826f2beff8b4c241ce8ede6beff619d83ec20538bd8e9b6dbbeff')
            for pos,destination in [(70,0x43e270),(79,0x43d740),(92,0x43cc16)]:
                struct.pack_into('<i',stub,pos,destination-(stub_address+pos+4))
            write(stub_address,bytes(stub))
            write(target,b'\xe9'+struct.pack('<i',stub_address-target-5)+b'\x90',True)
        for target,expected,label in hooks:
            patch=b'\xe9'+struct.pack('<i',assembled['labels'][label]-target-5)+b'\x90'*(len(expected)-5)
            write(target,patch,True)
        old=W.DWORD();check(k.VirtualProtectEx(pi.hProcess,address,4096,0x20,C.byref(old)),'Protect bridge RX')
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=checked['sha256']:
            raise ValueError('Game file changed during launch')
        result=dict(pid=pi.pid,verified=True,original_unchanged=True,verify_only=verify_only)
        if not verify_only:
            if k.ResumeThread(pi.hThread)==0xffffffff:raise OSError(C.get_last_error(),'Cannot resume game startup')
            resumed=True
        return result
    finally:
        # Only our own still-suspended child can be terminated here; existing games are untouched.
        if not resumed:k.TerminateProcess(pi.hProcess,1)
        k.CloseHandle(pi.hThread);k.CloseHandle(pi.hProcess)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('game');parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args();m=inspect_game(args.game)
    print(json.dumps(launch(args.game,m,args.verify_only),ensure_ascii=False))
