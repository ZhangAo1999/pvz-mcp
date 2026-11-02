import threading
import contextlib
import unittest
from unittest.mock import Mock, patch

from control_panel import Worker, Panel, action_params, describe
from board_view import lawn_model, plant_style, LawnCanvas, zombie_row_position, separate_zombies


class PanelTests(unittest.TestCase):
    def test_nearby_zombies_separate_and_do_not_jitter_on_refresh(self):
        zombies=[dict(id='a',row=4,x=620),dict(id='b',row=4,x=622)]
        offsets=separate_zombies(zombies,{})
        self.assertGreater(abs(offsets['a']-offsets['b']),.18)
        self.assertTrue(all(abs(offset)<.11 for offset in offsets.values()))
        moved=[dict(id='b',row=4,x=600),dict(id='a',row=4,x=601)]
        self.assertEqual(separate_zombies(moved,offsets),offsets)
        self.assertNotIn('a',separate_zombies(moved[:1],offsets))
    def test_native_vertical_motion_is_continuous(self):
        self.assertEqual(zombie_row_position(dict(row=2,y=150),5),1.5)
        self.assertEqual(zombie_row_position(dict(row=2,y=175),5),1.75)
        self.assertEqual(zombie_row_position(dict(row=3,y=220),6),2.5)
        self.assertEqual(zombie_row_position(dict(row=4),5),3.5)
    def test_board_preserves_stacked_plants_and_native_coordinates(self):
        snapshot = dict(terrain={'playable_rows': 6}, plants=[
            dict(type=16, row=3, col=1), dict(type=1,row=3,col=1),
            dict(type=30,row=3,col=1), dict(type=0,row=6,col=9)],
            zombies=[dict(row=3),dict(row=3),dict(row=6)])
        board = lawn_model(snapshot)
        self.assertEqual(board['rows'], 6)
        self.assertEqual([p['type'] for p in board['cells'][3,1]], [16,1,30])
        self.assertEqual(board['cells'][6,9][0]['type'], 0)
        self.assertEqual(board['zombies'][3], 2)
        self.assertEqual(plant_style(0)[0], '豌豆')
        self.assertEqual(plant_style(5)[0], '寒冰')
        self.assertNotEqual(plant_style(1)[1], plant_style(0)[1])
        self.assertEqual(plant_style(999)[0], '?')

    def test_snapshot_clears_on_replaced_board_or_process(self):
        panel = Mock(snapshot_key=('42:123',8))
        status = dict(attached=True, session_id='42:123',bridge={'epoch':8},scene={'board_present':True})
        Panel.validate_snapshot(panel,status)
        panel.lawn.show.assert_not_called()
        status['bridge']['epoch'] = 9
        Panel.validate_snapshot(panel,status)
        panel.lawn.show.assert_called_once_with(None)
        self.assertIsNone(panel.snapshot_key)

    def test_canvas_draws_color_layers_and_cell_health_details(self):
        import tkinter as tk
        root = tk.Tk(); root.withdraw()
        try:
            detail = tk.StringVar(root)
            canvas = LawnCanvas(root, detail)
            plants = [dict(type=16,row=3,col=1,hp=300,max_hp=300),
                      dict(type=1,row=3,col=1,hp=200,max_hp=300)]
            zombie = dict(type=2,row=3,x=200.0,hp=270,eating=True)
            snapshot = dict(terrain={'playable_rows':6},background_type=2,plants=plants,zombies=[zombie])
            with patch.object(canvas,'winfo_width',return_value=820),patch.object(canvas,'winfo_height',return_value=320):
                canvas.show(snapshot)
                items = canvas.find_withtag('cell-3-1')
                self.assertEqual(len(items),3)
                text = [i for i in items if canvas.type(i)=='text']
                self.assertEqual([canvas.itemcget(i,'text') for i in text],['睡莲','向日葵'])
                self.assertAlmostEqual(sum(canvas.coords(i)[1] for i in text)/2,32+(3-.5)*((320-32-12)/6))
                self.assertEqual(canvas.itemcget(text[1],'fill'),plant_style(1)[1])
                enemy = [i for i in canvas.find_withtag('zombie-0') if canvas.type(i)=='text'][0]
                self.assertEqual(canvas.itemcget(enemy,'text'),'路障')
                self.assertIn('17',canvas.itemcget(enemy,'font'))
                self.assertEqual(canvas.itemcget(enemy,'fill'),'#c42626')
                self.assertAlmostEqual(canvas.coords(enemy)[0],36+(200-40)/80*((820-36-88)/9))
                canvas.inspect_zombie(zombie)
                self.assertIn('正在啃咬',detail.get())
                canvas.inspect(3,1,plants)
                self.assertIn('向日葵',detail.get()); self.assertIn('200/300',detail.get())
                canvas.show(None)
                self.assertFalse(canvas.find_withtag('cell-3-1'))
        finally:
            root.destroy()

    def test_eating_label_does_not_cover_plant_name(self):
        import tkinter as tk
        root=tk.Tk();root.withdraw()
        try:
            canvas=LawnCanvas(root,tk.StringVar(root))
            snapshot=dict(terrain={'playable_rows':5},plants=[dict(type=3,row=5,col=6)],
                          zombies=[dict(id='attacker',type=0,row=5,x=480,y=450,eating=True)])
            with patch.object(canvas,'winfo_width',return_value=900),patch.object(canvas,'winfo_height',return_value=640):
                canvas.show(snapshot)
                plant=[i for i in canvas.find_withtag('cell-5-6') if canvas.type(i)=='text'][0]
                zombie=[i for i in canvas.find_withtag('zombie-0') if canvas.type(i)=='text'][0]
                p,z=canvas.bbox(plant),canvas.bbox(zombie)
                self.assertEqual(canvas.itemcget(zombie,'text'),'普僵')
                self.assertEqual(canvas.itemcget(zombie,'fill'),'#c42626')
                self.assertLess(z[3],p[1])
                self.assertAlmostEqual(z[0],(p[0]+p[2])/2,delta=3)
        finally:root.destroy()

    def test_complete_zombie_badges_stay_inside_their_row_after_avoidance(self):
        import tkinter as tk
        root=tk.Tk();root.withdraw()
        try:
            canvas=LawnCanvas(root,tk.StringVar(root))
            for rows in (5,6):
                snapshot=dict(terrain={'playable_rows':rows},
                    plants=[dict(type=3,row=r,col=6) for r in range(1,rows+1)],
                    zombies=[dict(id='r%d-%d'%(r,i),type=2,row=r,x=480+i,y=50+(r-1)*(85 if rows==6 else 100),eating=True)
                             for r in range(1,rows+1) for i in range(2)])
                with patch.object(canvas,'winfo_width',return_value=900),patch.object(canvas,'winfo_height',return_value=360):
                    canvas.show(snapshot)
                    ch=(360-32-12)/rows
                    for index,zombie in enumerate(snapshot['zombies']):
                        bounds=canvas.bbox('zombie-%d'%index)
                        self.assertGreaterEqual(bounds[1],32+(zombie['row']-1)*ch)
                        self.assertLessEqual(bounds[3],32+zombie['row']*ch)
        finally:root.destroy()

    def test_actions_bind_observed_session_and_epoch(self):
        observed = dict(attached=True, session_id='42:123', bridge={'epoch': 8})
        self.assertEqual(action_params('advance', observed, {'wait': False}),
                         dict(wait=False, expected_session_id='42:123', expected_board_epoch=8))
        with self.assertRaises(ValueError):
            action_params('resume', {})
        self.assertEqual(action_params('attach', {}, {'pid': 42, 'expected_session_id': '42:123'}),
                         dict(pid=42, expected_session_id='42:123'))

    def test_poll_never_reads_or_pauses_board(self):
        panel = Mock(busy=None,status={})
        Panel.poll(panel)
        panel.worker.submit.assert_called_once_with('status')
        self.assertEqual(panel.root.after.call_args.args[0],1000)
        panel.status = dict(scene={'board_attached':True})
        Panel.poll(panel)
        self.assertEqual(panel.root.after.call_args.args[0],250)
        panel.busy = 'advance'
        panel.worker.submit.reset_mock()
        Panel.poll(panel)
        panel.worker.submit.assert_not_called()

    def test_worker_deduplicates_poll_and_recovers_after_failure(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        class Fake:
            def execute(self, method, params):
                calls.append(method)
                if method == 'status':
                    started.set(); release.wait(2)
                    raise ValueError('closed')
                return dict(ok=True)
        worker = Worker(Fake)
        self.assertTrue(worker.submit('status'))
        self.assertTrue(started.wait(2))
        self.assertFalse(worker.submit('status'))
        self.assertTrue(worker.submit('list_games'))
        release.set()
        method, _, error = worker.results.get(timeout=3)
        self.assertEqual(method, 'status'); self.assertIn('closed', error)
        method, result, error = worker.results.get(timeout=3)
        self.assertEqual(method, 'list_games'); self.assertTrue(result['ok']); self.assertIsNone(error)
        self.assertEqual(calls, ['status', 'list_games'])

    def test_progress_display_distinguishes_native_pause_and_timed_advance(self):
        status = dict(running=True, attached=True, pid=42, scene={'code': 3, 'adventure_stage': '1-10'},
                      presentation=dict(phase='advancing', speed=1, next_decision_in_ms=1700))
        lines = describe(status)
        self.assertIn('1-10', lines[1]); self.assertIn('1.7', lines[2])
        status['presentation'] = dict(phase='native_pause', speed=1)
        self.assertIn('菜单暂停', describe(status)[2])

    def test_preferences_do_not_implicitly_attach_game(self):
        import pvz
        ctl = pvz.Controller()
        with patch.object(pvz, 'exclusive', return_value=contextlib.nullcontext()), \
                patch.object(ctl, 'connect', side_effect=pvz.PvZError('ATTACH_REQUIRED', 'not attached')) as connect, \
                patch.object(ctl, 'set_presentation', return_value={'game_connected': False}) as save:
            self.assertFalse(ctl.execute('set_presentation', {'speed': 1})['game_connected'])
            self.assertFalse(connect.call_args.kwargs['allow_attach'])
            save.assert_called_once_with({'speed': 1}, False)


if __name__ == '__main__':
    unittest.main()
