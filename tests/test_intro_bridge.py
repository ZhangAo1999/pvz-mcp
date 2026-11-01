"""Execute the real x86 pause/intro gate with mocked native Board::Update.

Optional developer dependency: unicorn. No game process is opened.
"""
import struct
import unittest
from bridge_asm import assemble
try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_EIP,UC_X86_REG_ESP,UC_X86_REG_ECX,UC_X86_REG_EDI
    HAVE_UNICORN=True
except ImportError:
    HAVE_UNICORN=False

@unittest.skipUnless(HAVE_UNICORN,'Optional x86 execution audit requires unicorn')
class IntroBridgeTests(unittest.TestCase):
    def run_bridge(self,scene=2,paused=1,speed=1,remaining=0,native_pause=False,transition=False):
        code,data,m,h=assemble();u=Uc(UC_ARCH_X86,UC_MODE_32)
        board,app,stack,stop=0x200000,0x300000,0x104000,0x100100
        for addr,size in [(0x100000,0x5000),(board,0x6000),(app,0x2000),(0x400000,0x200000),(m['code_va'],0x2000)]:u.mem_map(addr,size)
        u.mem_write(m['code_va'],code);u.mem_write(m['data_va'],data)
        def put(a,v):u.mem_write(a,struct.pack('<I',v))
        def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
        for key,v in dict(board=board,epoch=1,paused=paused,speed=speed,remaining=remaining,auto_pause_play=int(transition)).items():put(m['data_va']+m['mailbox'][key],v)
        put(board+0xa4,app);put(app+0x868,board);put(app+0x920,scene);put(app+0x974,2400 if scene==2 else 0xffffffff)
        put(board+0x160,board+0x5800);u.mem_write(board+0x5822,b'\x01')
        u.mem_write(board+0x17c,bytes([int(native_pause)]));put(stack,stop)
        u.reg_write(UC_X86_REG_ECX,board);u.reg_write(UC_X86_REG_EDI,0x12345678);u.reg_write(UC_X86_REG_ESP,stack)
        calls=[];m['ui_calls']=[]
        def native(uc,address,size,user):
            if address in (0x41a780,0x41e5b0,0x453010):
                m['ui_calls'].append(address)
                if address==0x41e5b0:
                    from unicorn.x86_const import UC_X86_REG_EAX
                    uc.reg_write(UC_X86_REG_EAX,1)
                sp=uc.reg_read(UC_X86_REG_ESP);uc.reg_write(UC_X86_REG_EIP,get(sp));uc.reg_write(UC_X86_REG_ESP,sp+4)
                return
            if address!=m['labels']['trampoline']:return
            calls.append(address)
            if transition:put(app+0x920,3)
            sp=uc.reg_read(UC_X86_REG_ESP);uc.reg_write(UC_X86_REG_EIP,get(sp));uc.reg_write(UC_X86_REG_ESP,sp+4)
        u.hook_add(UC_HOOK_CODE,native)
        u.emu_start(m['labels']['entry'],stop,count=10000)
        self.assertEqual(u.reg_read(UC_X86_REG_ESP),stack+4)
        self.assertEqual(u.reg_read(UC_X86_REG_ECX),board)
        self.assertEqual(u.reg_read(UC_X86_REG_EDI),0x12345678)
        result={k:get(m['data_va']+v) for k,v in m['mailbox'].items()}
        return u,m,result,calls,put,get,stack,stop
    def test_intro_refreshes_once_without_consuming_combat_budget(self):
        _,_,r,c,*_=self.run_bridge(speed=32,remaining=0)
        self.assertEqual(len(c),1);self.assertEqual(r['intro_ui_updates'],1)
        self.assertEqual(r['total_updates'],0);self.assertEqual(r['paused'],1)
    def test_combat_stays_completely_frozen_during_decision(self):
        _,m,r,c,*_=self.run_bridge(scene=3,speed=32)
        self.assertEqual(c,[]);self.assertEqual(r['total_updates'],0)
        self.assertEqual(m['ui_calls'],[0x41a780,0x41e5b0,0x453010])
    def test_menu_button_is_enabled_by_native_rules(self):
        u,_,_,_,*_=self.run_bridge(scene=3)
        self.assertEqual(u.mem_read(0x205822,1),b'\0')
    def test_native_menu_does_not_resume_combat_or_budget(self):
        _,m,r,c,*_=self.run_bridge(scene=3,native_pause=True)
        self.assertFalse(c);self.assertEqual(r['remaining'],0)
        self.assertEqual(m['ui_calls'],[0x41a780])
    def test_native_pause_still_stops_intro(self):
        _,_,r,c,*_=self.run_bridge(native_pause=True)
        self.assertFalse(c);self.assertEqual(r['intro_ui_updates'],0)
    def test_intro_to_combat_boundary_pauses_next_combat_tick(self):
        u,m,r,c,put,get,stack,stop=self.run_bridge(transition=True)
        u.reg_write(UC_X86_REG_ESP,stack);put(stack,stop)
        u.emu_start(m['labels']['entry'],stop,count=10000)
        self.assertEqual(len(c),1)
        self.assertEqual(get(m['data_va']+m['mailbox']['paused']),1)
        self.assertEqual(get(m['data_va']+m['mailbox']['total_updates']),0)
    def test_finite_combat_advance_keeps_exact_budget(self):
        _,_,r,c,*_=self.run_bridge(scene=3,remaining=3,speed=32)
        self.assertEqual(len(c),3);self.assertEqual(r['remaining'],0)
        self.assertEqual(r['total_updates'],3);self.assertEqual(r['intro_ui_updates'],0)
    def test_continuous_combat_still_honors_speed(self):
        _,_,r,c,*_=self.run_bridge(scene=3,paused=0,speed=2)
        self.assertEqual(len(c),2);self.assertEqual(r['total_updates'],2)

if __name__=='__main__':unittest.main()
