"""Live observation must fail closed without writing or pausing the simulation."""
import unittest
from unittest.mock import Mock, patch
import pvz


class ObserveTests(unittest.TestCase):
    def controller(self):
        ctl = object.__new__(pvz.Controller)
        ctl.session = 'fixture:1'
        token = dict(board=0x30000,epoch=7,app=0x20000,heartbeat=10,app_heartbeat=20,
                     request=3,ack=3,busy=0,app_busy=0,paused=0,remaining=200)
        ctl.board = Mock(return_value=(0x30000,token))
        ctl.mailbox = Mock(return_value=dict(token))
        ctl.mem = Mock(); ctl.mem.u32.return_value = 0x30000
        ctl.mem.write.side_effect = AssertionError('write forbidden')
        ctl.pause = Mock(side_effect=AssertionError('pause forbidden'))
        ctl.command = Mock(side_effect=AssertionError('command forbidden'))
        return ctl,token

    def test_running_board_observation_never_pauses_or_writes(self):
        ctl,token = self.controller()
        with patch('state.snapshot',return_value={'plants':[]}) as decode,patch('interaction.read',return_value={'conveyor':False}):
            result = ctl.observe()
        self.assertTrue(result['read_only']); self.assertEqual(result['board_epoch'],7)
        decode.assert_called_once()
        ctl.pause.assert_not_called(); ctl.command.assert_not_called(); ctl.mem.write.assert_not_called()
        self.assertEqual(token['remaining'],200); self.assertEqual(token['paused'],0)

    def test_mixed_frame_data_is_retried_without_pause(self):
        ctl,token = self.controller()
        changed = dict(token,heartbeat=11)
        ctl.mailbox.side_effect = [changed,dict(token)]
        with patch('state.snapshot',side_effect=[{'plants':[{'type':0}]},{'plants':[{'type':1}]}]),patch('interaction.read',return_value={'conveyor':False}):
            self.assertEqual(ctl.observe()['plants'][0]['type'],1)
        ctl.pause.assert_not_called()

    def test_unstable_reads_are_rejected_instead_of_pausing(self):
        ctl,token = self.controller()
        ctl.mailbox.return_value = dict(token,app_heartbeat=21)
        with patch('state.snapshot',return_value={'plants':[]}),patch('interaction.read',return_value={'conveyor':False}):
            with self.assertRaises(pvz.PvZError) as result:
                ctl.observe()
        self.assertEqual(result.exception.code,'UNSTABLE_SNAPSHOT')
        ctl.pause.assert_not_called(); ctl.mem.write.assert_not_called()

    def test_native_menu_can_be_observed_with_an_unchanged_pending_command(self):
        ctl,token = self.controller()
        token['request'] = 4
        ctl.mailbox.return_value = dict(token)
        with patch('state.snapshot',return_value={'plants':[]}),patch('interaction.read',return_value={'conveyor':False}):
            self.assertTrue(ctl.observe()['read_only'])
        ctl.command.assert_not_called()
