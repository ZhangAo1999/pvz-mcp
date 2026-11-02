import struct
import unittest
from background_extension import assemble, KILL_DIALOG
try:
    from unicorn import Uc,UC_ARCH_X86,UC_MODE_32,UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_ESP,UC_X86_REG_EIP,UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_ECX
    HAVE_UNICORN=True
except ImportError:
    HAVE_UNICORN=False


@unittest.skipUnless(HAVE_UNICORN,'Optional x86 audit requires unicorn')
class BackgroundTests(unittest.TestCase):
    def run_stub(self,paused=1,remaining=0,command=0,request=0,ack=0,native=True,pause_dialog=True,expected=0):
        u=Uc(UC_ARCH_X86,UC_MODE_32)
        app,board,mailbox,stub,stack,continuation,stop,trampoline=0x200000,0x210000,0x850000,0x900000,0x102000,0x100200,0x100300,0x100400
        for addr,size in [(0x100000,0x4000),(app,0x20000),(0x450000,0x10000),(mailbox,0x1000),(stub,0x1000)]:u.mem_map(addr,size)
        def put(a,v):u.mem_write(a,struct.pack('<I',v))
        def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
        put(app+0x920,3);put(app+0x868,board);u.mem_write(board+0x17c,bytes([int(native)]))
        for offset,value in [(16,board),(20,8),(28,paused),(32,remaining),(44,request),(48,ack),(52,command),(72,expected)]:put(mailbox+offset,value)
        u.mem_write(stub,assemble(stub,mailbox,trampoline,continuation));u.mem_write(continuation,b'\x61\xc3');put(stack,stop)
        u.reg_write(UC_X86_REG_ESI,app);u.reg_write(UC_X86_REG_EDI,mailbox);u.reg_write(UC_X86_REG_ECX,1234);u.reg_write(UC_X86_REG_ESP,stack)
        calls=[]
        def native_call(uc,address,size,_):
            if address not in (KILL_DIALOG,trampoline):return
            sp=uc.reg_read(UC_X86_REG_ESP)
            calls.append(address)
            if address==KILL_DIALOG:
                self.assertEqual(get(sp+4),19)
                self.assertEqual(uc.reg_read(UC_X86_REG_ECX),app)
                if pause_dialog:uc.mem_write(board+0x17c,b'\0')
            uc.reg_write(UC_X86_REG_EIP,get(sp));uc.reg_write(UC_X86_REG_ESP,sp+(8 if address==KILL_DIALOG else 4))
        u.hook_add(UC_HOOK_CODE,native_call)
        u.emu_start(stub,stop,count=1000)
        self.assertEqual(u.reg_read(UC_X86_REG_ESP),stack+4)
        self.assertEqual(u.reg_read(UC_X86_REG_ESI),app);self.assertEqual(u.reg_read(UC_X86_REG_EDI),mailbox)
        self.assertEqual(u.reg_read(UC_X86_REG_ECX),1234)
        return dict(calls=calls,paused=get(mailbox+28),remaining=get(mailbox+32),ack=get(mailbox+48),
                    result=get(mailbox+56),native=bool(u.mem_read(board+0x17c,1)[0]))

    def test_run_dismisses_native_pause_on_main_thread(self):
        r=self.run_stub(command=2,request=1)
        self.assertIn(KILL_DIALOG,r['calls']);self.assertFalse(r['native'])

    def test_finite_advance_does_not_lose_its_budget(self):
        r=self.run_stub(remaining=200)
        self.assertFalse(r['native']);self.assertEqual(r['remaining'],200)

    def test_continuous_running_dismisses_a_new_focus_pause(self):
        self.assertFalse(self.run_stub(paused=0)['native'])

    def test_decision_pause_does_not_dismiss_native_dialog(self):
        r=self.run_stub()
        self.assertNotIn(KILL_DIALOG,r['calls']);self.assertTrue(r['native'])

    def test_pause_command_is_acknowledged_without_board_updates(self):
        r=self.run_stub(paused=0,remaining=200,command=1,request=7,ack=6)
        self.assertEqual(r['ack'],7);self.assertEqual(r['paused'],1);self.assertEqual(r['remaining'],0)
        self.assertNotIn(KILL_DIALOG,r['calls'])

    def test_other_native_menus_are_preserved_and_command_rejected(self):
        r=self.run_stub(command=2,request=1,pause_dialog=False)
        self.assertTrue(r['native']);self.assertEqual(r['result'],23);self.assertEqual(r['ack'],1)

    def test_stale_run_never_dismisses_a_dialog(self):
        r=self.run_stub(command=2,request=1,expected=9)
        self.assertNotIn(KILL_DIALOG,r['calls']);self.assertEqual(r['result'],2)

    def test_paused_planting_can_dismiss_only_focus_pause(self):
        r=self.run_stub(command=5,request=1)
        self.assertFalse(r['native']);self.assertEqual(r['paused'],1)
