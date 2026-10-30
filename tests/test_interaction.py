"""Input-stage decoding and stale-card/plant protection without a game process."""
import struct
import unittest
from test_state import Fixture, put
import interaction
import guidance
from pvz import Controller, PvZError

class InteractionTests(unittest.TestCase):
    def fixture(self,scene=2,index=2400,tutorial=0):
        f=Fixture();f.app.extend(bytearray(0xa00-len(f.app)))
        put(f.app,0x920,scene);put(f.app,0x974,index,'i');put(f.b,0x559c,tutorial)
        put(f.b,0x5568,5);put(f.app,0x950,0)
        return f
    def test_chinese_dialogue_and_control_tags(self):
        f=self.fixture();text='你好，我的邻居！{SCREAM}';data=text.encode('utf-16le')
        f.mem.blocks[0x700000]=bytearray(data);put(f.app,0x97c,0x700000)
        put(f.app,0x98c,len(text));put(f.app,0x990,len(text))
        ctx=interaction.read(f.mem,f.APP,f.BOARD)
        self.assertEqual(ctx['dialogue']['text'],'你好，我的邻居！')
        self.assertEqual(ctx['dialogue']['raw_text'],text)
        h=guidance.hints(dict(scene=2,board_epoch=9,interaction=ctx))
        self.assertEqual(h['example']['arguments'],dict(expected_board_epoch=9,expected_message_index=2400))
    def test_shovel_transition_and_combat_availability(self):
        for state in (15,16,17):
            f=self.fixture(index=-1,tutorial=state);ctx=interaction.read(f.mem,f.APP,f.BOARD)
            self.assertEqual(ctx['phase'],'shovel_tutorial');self.assertTrue(ctx['shovel']['available'])
        f=self.fixture(index=-1,tutorial=18);self.assertEqual(interaction.read(f.mem,f.APP,f.BOARD)['phase'],'intro')
        put(f.app,0x920,3);ctx=interaction.read(f.mem,f.APP,f.BOARD)
        self.assertTrue(ctx['conveyor']);self.assertFalse(ctx['shovel']['available'])
        self.assertNotIn('pvz_shovel',guidance.hints(dict(scene=3,interaction=ctx))['next_actions'])
    def test_corrupt_dialogue_bounds_rejected(self):
        f=self.fixture();put(f.app,0x98c,4097);put(f.app,0x990,8192)
        with self.assertRaises(ValueError):interaction.read(f.mem,f.APP,f.BOARD)
    def test_card_revision_changes_with_time_and_compaction(self):
        f=Fixture();a=f.decode()['cards_revision'];put(f.b,0x5584,1401)
        b=f.decode()['cards_revision'];self.assertNotEqual(a,b)
        put(f.bank,0x28+0x34,1);self.assertNotEqual(b,f.decode()['cards_revision'])
    def test_empty_packet_is_not_selectable(self):
        f=Fixture();put(f.bank,0x28+0x34,-1,'i');card=f.decode()['cards'][0]
        self.assertEqual(card['name'],'EMPTY');self.assertFalse(card['occupied']);self.assertFalse(card['selectable_flag'])
    def test_help_does_not_require_game_configuration(self):
        self.assertIn('workflow',Controller().execute('help',{}))
    def test_seed_help_example_contains_type_ids_and_correct_count(self):
        h=guidance.hints(dict(scene=2,board_epoch=3,seed_selection=dict(required_count=2,available_seeds=[dict(type=0),dict(type=1),dict(type=3)])))
        self.assertEqual(h['example']['arguments'],dict(expected_board_epoch=3,seeds=[0,1]))
    def test_reward_hint_uses_current_coin_id_then_waits_for_settlement(self):
        s=dict(scene=3,board_epoch=7,coins=[dict(type=7,id='7:coins:10000',collecting=False)])
        h=guidance.hints(s);self.assertEqual(h['next_actions'],['pvz_collect'])
        self.assertEqual(h['example']['arguments']['coin_id'],'7:coins:10000')
        s['coins'][0]['collecting']=True
        self.assertEqual(guidance.hints(s)['next_actions'],['pvz_advance'])

class GuardFixture(Controller):
    def __init__(self,state):self.value=state;self.mem=self;self.commands=[]
    def board(self):return 0x10000,{}
    def check_epoch(self,*args):return 7
    def pause(self):pass
    def state(self):return self.value
    def command(self,*args,**kwargs):self.commands.append(args);raise AssertionError('Stale input must never reach native command')

class MutationGuardTests(unittest.TestCase):
    def test_stale_dialogue_rejected_before_native_call(self):
        c=GuardFixture(dict(mode=0,level=5,interaction=dict(phase='dialogue',dialogue=dict(message_index=2401))))
        with self.assertRaises(PvZError) as e:c.dispatch('continue_dialogue',dict(expected_message_index=2400))
        self.assertEqual(e.exception.code,'STALE_DIALOGUE');self.assertFalse(c.commands)
    def test_stale_plant_rejected_before_native_call(self):
        c=GuardFixture(dict(interaction=dict(shovel=dict(available=True)),plants=[]))
        with self.assertRaises(PvZError) as e:c.dispatch('shovel',dict(plant_id='old',row=2,col=6))
        self.assertEqual(e.exception.code,'STALE_ENTITY');self.assertFalse(c.commands)
    def test_stale_cards_rejected_before_native_call(self):
        c=GuardFixture(dict(scene=3,mode=0,seed_bank=dict(kind='conveyor'),cards_revision='current'))
        with self.assertRaises(PvZError) as e:c.dispatch('place_plant',dict(expected_cards_revision='old'))
        self.assertEqual(e.exception.code,'STALE_CARDS');self.assertFalse(c.commands)
    def test_planting_during_dialogue_returns_recovery_hint(self):
        c=GuardFixture(dict(scene=2,guidance=dict(next_actions=['pvz_continue_dialogue'])))
        with self.assertRaises(PvZError) as e:c.dispatch('place_plant',{})
        self.assertEqual(e.exception.code,'NOT_PLAYING')
        self.assertIn('guidance',e.exception.details);self.assertFalse(c.commands)

if __name__=='__main__':unittest.main()
