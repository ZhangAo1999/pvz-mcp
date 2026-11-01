import unittest
import rewards
import guidance
from collect_extension import assemble
import struct
try:
    from unicorn import Uc,UC_ARCH_X86,UC_MODE_32,UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EIP,UC_X86_REG_ESP,UC_X86_REG_ECX
    HAVE_UNICORN=True
except ImportError:
    HAVE_UNICORN=False

class RewardsTests(unittest.TestCase):
    def test_letter_is_not_a_money_bag(self):
        self.assertEqual(rewards.NAMES[15],'ZOMBIE_NOTE')
        self.assertEqual(rewards.NAMES[18],'AWARD_MONEY_BAG')
        self.assertEqual(rewards.NAMES[20],'AWARD_BAG_DIAMOND')
    def test_note_guidance_and_transcription(self):
        coin=dict(id='4:coins:12340000',type=15,name='ZOMBIE_NOTE',collecting=False)
        r=rewards.lawn_reward(coin,9,0)
        self.assertIn('发动全面进贡',r['letter']['text'])
        self.assertEqual(r['letter']['next_adventure_level'],10)
        h=guidance.hints(dict(scene=3,board_epoch=4,coins=[coin]))
        self.assertEqual(h['next_actions'],['pvz_collect'])
        coin['collecting']=True
        self.assertEqual(guidance.hints(dict(scene=3,coins=[coin]))['next_actions'],['pvz_advance'])
    def test_note_award_uses_next_level_only_in_adventure(self):
        self.assertIsNone(rewards.award_info(3,0,10))
        self.assertIsNone(rewards.award_info(5,16,10))
        self.assertEqual(rewards.award_info(5,0,10)['letter']['completed_adventure_level'],9)
        self.assertIsNone(rewards.award_info(5,0,11)['letter'])
    def test_unknown_special_objects_fail_closed(self):
        self.assertNotIn(16,rewards.PICKUP_TYPES)
        self.assertNotIn(17,rewards.PICKUP_TYPES)
    def test_native_allowlist_keeps_old_stub_reproducible(self):
        old=assemble(0x900000,0x851000,0x850b00,legacy=True)
        new=assemble(0x900000,0x851000,0x850b00)
        self.assertGreater(len(new),len(old))
        for value in (15,18,20):self.assertIn(bytes([0x83,0xf8,value]),new)

@unittest.skipUnless(HAVE_UNICORN,'Optional x86 audit requires unicorn')
class PickupNativeTests(unittest.TestCase):
    def execute_pickup(self,type_id,stale=False):
        u=Uc(UC_ARCH_X86,UC_MODE_32)
        code,mailbox,app,board,pool,ack,stack=0x900000,0x851000,0x300000,0x200000,0x210000,0x901000,0x104000
        for address,size in [(0x100000,0x5000),(board,0x20000),(app,0x2000),(0x400000,0x100000),(0x850000,0x2000),(code,0x2000)]:u.mem_map(address,size)
        def put(a,v):u.mem_write(a,struct.pack('<I',v))
        def get(a):return struct.unpack('<I',u.mem_read(a,4))[0]
        put(app+0x920,3);put(app+0x868,board);put(mailbox+0x10,board);put(mailbox+0x1c,1)
        put(mailbox+0x34,14);put(mailbox+0x3c,0x12340000)
        put(board+0xfc,pool);put(board+0x100,1);put(board+0x104,1)
        put(pool+4,board);put(pool+0xd0,0x43210000 if stale else 0x12340000);put(pool+0x58,type_id)
        u.mem_write(code,assemble(code,mailbox,ack));u.reg_write(UC_X86_REG_ESI,app);u.reg_write(UC_X86_REG_EDI,mailbox);u.reg_write(UC_X86_REG_ESP,stack)
        calls=[]
        def native(uc,address,size,_):
            if address not in (0x43e270,0x43d740):return
            calls.append(address)
            if address==0x43d740:self.assertEqual(uc.reg_read(UC_X86_REG_ECX),pool)
            sp=uc.reg_read(UC_X86_REG_ESP);uc.reg_write(UC_X86_REG_EIP,get(sp));uc.reg_write(UC_X86_REG_ESP,sp+4)
        u.hook_add(UC_HOOK_CODE,native);u.emu_start(code,ack,count=3000)
        self.assertEqual(u.reg_read(UC_X86_REG_ESP),stack)
        return get(mailbox+0x38),calls
    def test_native_letter_and_bags_use_original_pickup(self):
        for value in (1,7,9,15,18,20):
            with self.subTest(value=value):self.assertEqual(self.execute_pickup(value),(0,[0x43e270,0x43d740]))
    def test_other_objects_and_stale_ids_never_reach_pickup(self):
        self.assertEqual(self.execute_pickup(16),(21,[]))
        self.assertEqual(self.execute_pickup(15,stale=True),(20,[]))

if __name__=='__main__':unittest.main()
