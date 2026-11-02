"""Read-only process discovery. Does not inject code or alter configuration."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
from runtime_bridge import inspect_game

def likely_game(path,configured=None):
    name=Path(path).name.lower()
    return (name in ('plantsvszombies.exe','popcapgame1.exe') or
            '植物大战僵尸' in name or
            bool(configured and os.path.normcase(os.path.abspath(path))==os.path.normcase(os.path.abspath(configured))))

def enumerate_games(configured=None,pid=None):
    k=C.WinDLL('kernel32',use_last_error=True);p=C.WinDLL('psapi',use_last_error=True)
    p.EnumProcesses.argtypes=[C.POINTER(W.DWORD),W.DWORD,C.POINTER(W.DWORD)];p.EnumProcesses.restype=W.BOOL
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
    k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)];k.QueryFullProcessImageNameW.restype=W.BOOL
    k.GetProcessTimes.argtypes=[W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)];k.GetProcessTimes.restype=W.BOOL
    k.CloseHandle.argtypes=[W.HANDLE]
    ids=(W.DWORD*16384)();needed=W.DWORD()
    if not p.EnumProcesses(ids,C.sizeof(ids),C.byref(needed)):raise C.WinError(C.get_last_error())
    result=[]
    for current in ids[:needed.value//4]:
        if pid is not None and current!=pid:continue
        h=k.OpenProcess(0x1000,False,current)
        if not h:continue
        try:
            path=C.create_unicode_buffer(32768);size=W.DWORD(len(path))
            if not k.QueryFullProcessImageNameW(h,0,path,C.byref(size)):continue
            if pid is None and not likely_game(path.value,configured):continue
            times=[W.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h,*[C.byref(t) for t in times]):continue
            session='%d:%d'%(current,(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
            value=dict(pid=current,session_id=session,game=path.value,supported=False)
            try:
                manifest=inspect_game(path.value)
                value.update(supported=True,sha256=manifest['sha256'])
            except (OSError,ValueError) as ex:value['reason']=str(ex)
            result.append(value)
        finally:k.CloseHandle(h)
    return result

def select_game(games,pid=None):
    candidates=[x for x in games if (pid is None or x['pid']==pid) and x['supported']]
    if len(candidates)==1:return candidates[0]
    if len(candidates)>1:raise ValueError('AMBIGUOUS_PROCESS')
    if games:raise ValueError('UNSUPPORTED_GAME')
    raise ValueError('NOT_RUNNING')
