"""Spectator-visible conveyor policy, including hidden-slot mutation guards."""
import unittest
from test_state import Fixture, put
from test_interaction import GuardFixture
from pvz import PvZError
import state


class ConveyorVisibilityTests(unittest.TestCase):
    def deck(self, x=591, width=50, scene=3, shown=True):
        f=Fixture()
        put(f.app,0x920,scene)
        for index in range(2):
            base=0x28+index*0x50
            put(f.bank,base+8,x,'i');put(f.bank,base+0x10,width,'i')
            put(f.bank,base+0x30,0)
            f.bank[base+0x18]=int(shown)
        return f

    def decode(self,f):return state.snapshot(f.mem,f.BOARD,7,conveyor=True)

    def test_card_not_exposed_until_twenty_percent(self):
        for x in (606,591,590,582):
            with self.subTest(x=x):
                s=self.decode(self.deck(x))
                self.assertEqual(s['cards'],[])
                self.assertEqual(s['counts']['cards'],0)
                self.assertNotIn('WALL_NUT',str(s['cards']))
        s=self.decode(self.deck(581))
        self.assertEqual(s['cards'][0]['visibility']['visible_fraction'],.2)
        self.assertEqual(s['cards'][0]['visibility']['visible_pixels'],10)

    def test_packet_offset_controls_drawing_position(self):
        f=self.deck(91);put(f.bank,0x28+0x30,491)
        self.assertEqual([c['slot'] for c in self.decode(f)['cards']],[2])
        put(f.bank,0x28+0x30,490)
        self.assertEqual([c['slot'] for c in self.decode(f)['cards']],[1,2])

    def test_filter_preserves_native_slots_and_omits_hidden_identity(self):
        f=self.deck(581);put(f.bank,0x28+8,582)
        cards=self.decode(f)['cards']
        self.assertEqual([c['slot'] for c in cards],[2])
        self.assertNotIn('_conveyor_geometry',str(cards))
        self.assertEqual(cards[0]['type'],48)

    def test_clip_both_edges_and_reject_invalid_geometry(self):
        for x,width,expected in ((41,50,0),(50,50,2),(90,50,2),(900,50,0),(581,0,0),(581,-1,0)):
            with self.subTest(x=x,width=width):
                self.assertEqual(len(self.decode(self.deck(x,width))['cards']),expected)

    def test_empty_hidden_or_intro_packets_are_not_exposed(self):
        for f in (self.deck(100,scene=2),self.deck(100,shown=False)):
            self.assertEqual(self.decode(f)['cards'],[])
        f=self.deck(100)
        for i in range(2):put(f.bank,0x28+i*0x50+0x34,-1,'i')
        self.assertEqual(self.decode(f)['cards'],[])

    def test_ordinary_deck_keeps_empty_slots_and_has_no_internal_geometry(self):
        f=self.deck(900)
        put(f.bank,0x28+0x34,-1,'i')
        s=f.decode()
        self.assertEqual([c['slot'] for c in s['cards']],[1,2])
        self.assertEqual(s['cards'][0]['type'],-1)
        self.assertNotIn('_conveyor_geometry',str(s))

    def test_hidden_identity_does_not_change_public_revision(self):
        f=self.deck(591);a=self.decode(f)['cards_revision']
        put(f.bank,0x28+0x34,49)
        self.assertEqual(a,self.decode(f)['cards_revision'])
        put(f.bank,0x28+8,581)
        self.assertNotEqual(a,self.decode(f)['cards_revision'])

    def test_guessing_hidden_slot_never_reaches_native_input(self):
        for method in ('can_place','place_plant'):
            c=GuardFixture(dict(scene=3,mode=0,seed_bank=dict(kind='conveyor'),cards_revision='current',cards=[dict(slot=2)]))
            with self.assertRaises(PvZError) as e:
                c.dispatch(method,dict(expected_cards_revision='current',card_slot=1))
            self.assertEqual(e.exception.code,'CARD_NOT_VISIBLE')
            self.assertFalse(c.commands)


if __name__=='__main__':unittest.main()
