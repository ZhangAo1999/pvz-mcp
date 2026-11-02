"""Discovery, process selection and injection invariants without a real game."""
import contextlib
import ctypes as C
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from artifacts import Artifacts
import attach
import discovery
from bridge_asm import assemble

class DiscoveryTests(unittest.TestCase):
    def test_configured_path_is_optional(self):
        self.assertTrue(discovery.likely_game(r'D:\Other\PlantsVsZombies.exe'))
        self.assertTrue(discovery.likely_game(r'D:\Other\植物大战僵尸.exe'))
        self.assertFalse(discovery.likely_game(r'D:\Other\notepad.exe'))
        self.assertTrue(discovery.likely_game(r'D:\Other\renamed.exe',r'D:\Other\renamed.exe'))
    def test_multiple_games_require_selection(self):
        games=[dict(pid=i,supported=True) for i in (1,2)]
        with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):discovery.select_game(games)
        self.assertEqual(discovery.select_game(games,2)['pid'],2)
    def test_unsupported_is_never_a_fallback(self):
        games=[dict(pid=1,supported=False),dict(pid=2,supported=True)]
        self.assertEqual(discovery.select_game(games)['pid'],2)
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED'):discovery.select_game(games,1)
        with self.assertRaisesRegex(ValueError,'NOT_RUNNING'):discovery.select_game([])
    def test_relocated_bridge_is_self_consistent(self):
        a=assemble()[2];code,mailbox,b,_=assemble(code_address=0x1000000,data_address=0x1001000)
        self.assertEqual(b['labels']['entry']-a['labels']['entry'],0x1000000-a['code_va'])
        self.assertEqual(b['data_va'],0x1001000)
        self.assertLess(len(code),0xe00);self.assertEqual(mailbox[:4],b'PZCT')

class ThreadAPI:
    def __init__(self,ip=0x401000):self.suspended=[];self.resumed=[];self.closed=[];self.ip=ip
    def OpenThread(self,*args):return args[-1]
    def SuspendThread(self,h):self.suspended.append(h);return 0
    def ResumeThread(self,h):self.resumed.append(h);return 1
    def CloseHandle(self,h):self.closed.append(h)
    def Wow64GetThreadContext(self,h,context):
        struct.pack_into('<I',context._obj,184,self.ip);return 1

class ThreadTests(unittest.TestCase):
    def test_suspension_always_balanced_on_write_failure(self):
        k=ThreadAPI()
        with patch.object(attach,'thread_ids',return_value=[11,12]):
            with self.assertRaisesRegex(RuntimeError,'write failed'):
                with attach.frozen(k,42,[]):raise RuntimeError('write failed')
        self.assertEqual(k.suspended,[11,12]);self.assertEqual(k.resumed,[12,11])
        self.assertEqual(k.closed,[12,11])
    def test_instruction_inside_detour_is_never_patched(self):
        k=ThreadAPI(0x4211c1);entered=[]
        with patch.object(attach,'thread_ids',return_value=[11]),patch.object(attach.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'remained inside'):
                with attach.frozen(k,42,[(0x4211c0,0x4211c7)]):entered.append(True)
        self.assertFalse(entered);self.assertEqual(len(k.suspended),len(k.resumed))

class BytesMemory:
    def __init__(self):self.data={};self.writes=[]
    def read(self,a,n):return bytes(self.data.get(a+i,0) for i in range(n))
    def write(self,a,data):
        self.writes.append((a,data))
        for i,v in enumerate(data):self.data[a+i]=v
    def u32(self,a):return struct.unpack('<I',self.read(a,4))[0]
    def i32(self,a):return struct.unpack('<i',self.read(a,4))[0]
    def put(self,a,v):self.write(a,struct.pack('<I',v))

class AllocationAPI:
    def __init__(self,relocate=False):self.relocate=relocate
    def VirtualAllocEx(self,h,address,*args):return (None if address else 0x1200000) if self.relocate else address
    def VirtualFreeEx(self,*args):return 1
    def VirtualProtectEx(self,h,a,n,p,old):old._obj.value=0x20;return 1
    def FlushInstructionCache(self,*args):return 1

class InstallTests(unittest.TestCase):
    def fixture(self):
        memory=BytesMemory();app,board=0x300000,0x200000
        memory.put(0x755e0c,app);memory.put(app+0x868,board);memory.put(app+0x920,3)
        memory.write(board,b'unchanged plants, zombies, sun and timers')
        for target,old,_ in assemble()[3]:memory.write(target,old)
        memory.write(0x43cc10,bytes.fromhex('83ec20538bd8'));memory.writes=[]
        return SimpleNamespace(path='verified.exe',pid=42,session='42:123',mem=memory,handle=99),board
    def test_attachment_preserves_native_board_and_relocates(self):
        c,b=self.fixture();before=c.mem.read(b,0x5700)
        with Artifacts() as folder,patch.object(attach,'DATA',Path(folder)),patch.object(attach,'api',return_value=AllocationAPI(True)),patch.object(attach,'frozen',return_value=contextlib.nullcontext()),patch.object(attach,'inspect_game',return_value=dict(game='verified.exe',sha256='verified')):
            record=attach.attach(c)
            self.assertTrue(record['native_board_bytes_unchanged'])
            self.assertEqual(record['manifest']['code_va'],0x1200000)
            self.assertEqual(c.mem.read(b,0x5700),before)
            self.assertFalse(any(b<=a<b+0x5700 for a,_ in c.mem.writes))
    def test_foreign_hook_is_rejected_before_any_write(self):
        c,_=self.fixture();c.mem.write(0x4211c0,b'OTHER!!');c.mem.writes=[]
        with patch.object(attach,'api',return_value=AllocationAPI()),patch.object(attach,'frozen',return_value=contextlib.nullcontext()),patch.object(attach,'inspect_game',return_value={}):
            with self.assertRaisesRegex(ValueError,'Original hook differs'):attach.attach(c)
        self.assertFalse(c.mem.writes)

if __name__=='__main__':unittest.main()
