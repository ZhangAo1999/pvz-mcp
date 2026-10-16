"""Regression: native EndLevel can destroy Board outside its update hook.

Uses a memory fixture only; never opens or controls a game process.
"""
import unittest
from pvz import Controller, PvZError


class AdvanceFixture(Controller):
    def __init__(self, outcome):
        self.outcome=outcome;self.closed=False;self.running=False;self.pauses=0
        self.session='fixture'
        self.mem=self
    def u32(self,address):
        assert address==0x20868
        return 0 if self.closed else 0x30000
    def i32(self,address):
        if address==0x35584:return 100
        if address==0x20920:return 5 if self.closed else 3
        raise AssertionError(hex(address))
    def mailbox(self):
        return dict(board=0x30000,epoch=3,app=0x20000,busy=0,app_busy=0,
                    remaining=42 if self.closed else 0,stop=0 if self.closed else 1,
                    total_updates=708 if self.closed else 750 if self.running else 100)
    def board(self):
        if self.closed:raise PvZError('BOARD_CHANGED','Native App destroyed Board')
        return 0x30000,self.mailbox()
    def pause(self):
        if self.closed:raise AssertionError('Must not send a Board command after EndLevel')
        self.pauses+=1
    def command(self,cmd,*args,**kw):
        assert cmd==3
        self.running=True
    def prepare_advance(self):return 10.0,'fixture'
    def wait(self,predicate,timeout=8):
        # Reproduce the real 1-4 trace: 608/650 updates, award scene, 42 unused.
        self.closed=self.outcome!='budget'
        if self.outcome=='timeout_race':raise PvZError('TIMEOUT','Fixture boundary race')
        pending=self.mailbox();pending['app_busy']=1
        assert not predicate(pending),'Do not inspect a scene mid-update'
        done=self.mailbox()
        assert predicate(done),'A native transition must end the wait promptly'
        return done
    def state(self):
        if self.closed:raise AssertionError('Must not decode a freed Board')
        return dict(counters=dict(board_updates=750))
    def status(self):
        return dict(scene=dict(code=5,profile_adventure_level=5))


class TransitionTests(unittest.TestCase):
    def run_fixture(self,outcome):
        ctl=AdvanceFixture(outcome)
        result=ctl.dispatch('advance',dict(ticks=650,speed=16,expected_board_epoch=3))
        self.assertEqual(ctl.pauses,1)
        return result
    def test_award_transition_returns_progress_instead_of_error(self):
        r=self.run_fixture('award')
        self.assertEqual(r['advanced_updates'],608)
        self.assertEqual(r['stop_reason'],'board_changed')
        self.assertIsNone(r['state'])
        self.assertEqual(r['status']['scene']['profile_adventure_level'],5)
    def test_transition_at_timeout_does_not_pause_freed_board(self):
        r=self.run_fixture('timeout_race')
        self.assertEqual(r['stop_reason'],'board_changed')
        self.assertEqual(r['advanced_updates'],608)
    def test_normal_budget_still_returns_board_snapshot(self):
        r=self.run_fixture('budget')
        self.assertEqual(r['advanced_updates'],650)
        self.assertEqual(r['board_update_delta'],650)
        self.assertEqual(r['stop_reason'],'budget_exhausted')
    def test_async_returns_without_wait_or_decoding_a_running_board(self):
        ctl=AdvanceFixture('budget')
        def forbidden(*args,**kwargs):raise AssertionError('Async must return without waiting or decoding live entities')
        ctl.wait=forbidden;ctl.state=forbidden
        r=ctl.dispatch('advance',dict(ticks=650,speed=2,wait=False,expected_board_epoch=3))
        self.assertTrue(r['asynchronous'])
        self.assertEqual(r['estimated_wait_ms'],3250)
        self.assertEqual(ctl.pauses,1)


if __name__=='__main__':unittest.main(verbosity=2)
