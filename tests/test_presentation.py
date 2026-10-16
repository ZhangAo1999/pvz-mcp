from artifacts import Artifacts
"""Spectator regressions: clock continuity, measured ETA and bounded controls."""
import unittest
import tempfile
import contextlib, json, threading
from pathlib import Path
from unittest.mock import patch
import presentation as ui
from pvz import Controller

def status(phase='decision',tick=100,remaining=0,speed=2):
    return dict(session_id='test:123',scene=dict(code=5 if phase=='award' else 3,
        name='award' if phase=='award' else 'playing',board_attached=phase!='award',game_pause_flag=phase=='native_pause'),
        bridge=dict(epoch=3,paused=int(phase!='running'),remaining=remaining,speed=speed,
                    total_updates=tick,heartbeat=tick))

class PresentationTests(unittest.TestCase):
    def test_atomic_write_retries_a_temporary_windows_reader_lock(self):
        with Artifacts(dir=r'D:\Temp\Codex\PvZ-Toolset') as folder:
            target=Path(folder)/'commentary.json';target.write_text('{"old":true}',encoding='utf-8')
            replace=ui.os.replace;attempts=[]
            def locked_once(source,destination):
                attempts.append(source)
                if len(attempts)==1:
                    self.assertEqual(ui.read_json(target),{'old':True})
                    raise PermissionError(5,'temporary reader lock')
                return replace(source,destination)
            with patch.object(ui.os,'replace',side_effect=locked_once),patch.object(ui.time,'sleep') as sleep:
                ui.write_json(target,{'plan':'继续本局，不重开。'})
            self.assertEqual(len(attempts),2)
            self.assertEqual(ui.read_json(target),{'plan':'继续本局，不重开。'})
            sleep.assert_called_once_with(.01)
    def test_explanation_stays_stable_across_pause_and_advance(self):
        with Artifacts(dir=r'D:\Temp\Codex\PvZ-Toolset') as folder,patch.object(ui,'RUNTIME',Path(folder)):
            clock=ui.HudClock();s=status(tick=100)
            initial=clock.update(s,10)
            self.assertEqual(initial['presentation_state'],'thinking')
            self.assertIsNone(initial['plan'])
            ui.set_commentary(dict(plan='先补阳光产量。'),s)
            publishing=clock.update(s,13)
            self.assertEqual(publishing['presentation_state'],'thinking')
            self.assertEqual(publishing['elapsed_seconds'],3)
            self.assertEqual(publishing['action'],'等待执行本轮操作。')
            ui.set_commentary(dict(action='补种两株向日葵，下路增加一株豌豆射手。'),s)
            advance=clock.update(status('advancing',tick=200,remaining=100),14)
            self.assertEqual(advance['plan'],'先补阳光产量。')
            self.assertIn('两株向日葵',advance['action'])
            following=clock.update(status(tick=300),16)
            self.assertEqual(following['presentation_state'],'thinking')
            self.assertEqual(following['commentary_label'],'最近操作说明')
            self.assertEqual(following['plan'],'先补阳光产量。')
            self.assertEqual(following['elapsed_seconds'],0)
            with patch.object(ui.time,'time',return_value=ui.read_json(ui.RUNTIME/'commentary.json')['time']+31):
                self.assertEqual(clock.update(status(tick=300),17)['plan'],'先补阳光产量。')
            other=status();other['session_id']='restarted:456'
            self.assertIsNone(ui.HudClock().update(other)['plan'])
    def test_rapid_new_caption_is_queued_without_blocking_game_or_flashing(self):
        with Artifacts(dir=r'D:\Temp\Codex\PvZ-Toolset') as folder,patch.object(ui,'RUNTIME',Path(folder)),patch.object(ui,'settings',return_value=dict(ui.DEFAULTS,commentary_ms=4000)):
            clock=ui.HudClock();s=status()
            ui.set_commentary(dict(plan='先发展。',action='种了向日葵。'),s)
            self.assertEqual(clock.update(s,0)['plan'],'先发展。')
            ui.set_commentary(dict(plan='补火力。',action='种了豌豆。'),status(tick=300))
            self.assertEqual(clock.update(status('advancing',tick=300,remaining=50),1)['plan'],'先发展。')
            self.assertEqual(clock.update(status(tick=500),3)['plan'],'先发展。')
            self.assertEqual(clock.update(status(tick=500),4.1)['plan'],'补火力。')
    def test_optional_caption_write_failure_does_not_fail_the_game_tool(self):
        with patch.object(ui,'write_json',side_effect=PermissionError('reader lock')),patch.object(ui,'warn'):
            result=ui.set_commentary(dict(plan='补火力。',action='种了豌豆。'),status())
        self.assertFalse(result['display_updated'])
    def test_native_action_receipt_survives_optional_display_failures(self):
        with Artifacts(dir=r'D:\Temp\Codex\PvZ-Toolset') as folder:
            root=Path(folder);(root/'logs').mkdir();ctl=Controller.__new__(Controller);ctl.identity=None;ctl.base=0x851000;ctl.M={"speed":36};ctl.session='test:123'
            result={'placed':True,'created':[dict(type=0,row=1,col=2)]}
            with patch('pvz.DATA',root),patch('pvz.exclusive',return_value=contextlib.nullcontext()),patch.object(ctl,'connect'),patch.object(ctl,'close'),patch.object(ctl,'dispatch',return_value=result) as dispatch,patch.object(ui,'ensure_overlay',side_effect=OSError('overlay unavailable')),patch.object(ui,'annotate',side_effect=OSError('caption locked')),patch.object(ui,'warn'),patch('selection.finish_if_left'):
                self.assertEqual(ctl.execute('place_plant',{}),result)
                dispatch.assert_called_once()
            self.assertEqual(json.loads((root/'logs'/'commands.jsonl').read_text(encoding='utf-8').splitlines()[-1])['phase'],'complete')
    def test_shared_readers_do_not_block_windows_atomic_replacement(self):
        with Artifacts(dir=r'D:\Temp\Codex\PvZ-Toolset') as folder:
            target=Path(folder)/'stress.json';ui.write_json(target,dict(index=0));stop=threading.Event();errors=[]
            def reader():
                while not stop.is_set():
                    if 'index' not in ui.read_json(target):errors.append('partial document')
            threads=[threading.Thread(target=reader) for _ in range(3)]
            for thread in threads:thread.start()
            try:
                for index in range(150):ui.write_json(target,dict(index=index))
            finally:
                stop.set()
                for thread in threads:thread.join()
            self.assertFalse(errors)
    def test_new_decision_plan_does_not_reuse_previous_action(self):
        with Artifacts(dir=r'D:\Temp\Codex\PvZ-Toolset') as folder,patch.object(ui,'RUNTIME',Path(folder)):
            ui.set_commentary(dict(plan='先发展。',action='种了向日葵。'),status(tick=100))
            note=ui.set_commentary(dict(plan='下路补火力。'),status(tick=200))
            self.assertNotIn('action',note)
    def test_game_time_defaults_and_overrides_are_independent_of_caption_duration(self):
        with patch.object(ui,'settings',return_value=dict(ui.DEFAULTS)):
            self.assertEqual(ui.advance_request({}),(200,2000))
            self.assertEqual(ui.advance_request({'duration_ms':3500}),(350,3500))
            self.assertEqual(ui.advance_request({'ticks':123}),(123,1230))
            for value in [{'ticks':True},{'duration_ms':2001},{'ticks':100,'duration_ms':1000}]:
                with self.assertRaises(ValueError):ui.advance_request(value)
    def test_commentary_does_not_add_any_pre_advance_wait(self):
        with patch.object(ui,'settings',return_value=dict(ui.DEFAULTS)),patch.object(ui.time,'sleep') as sleep,patch.object(ui,'read_json',return_value=dict(session_id='test:123',time=ui.time.time())):
            ui.hold_for_spectator('test:123')
            sleep.assert_not_called()
    def test_captions_wrap_chinese_and_elide_at_two_lines(self):
        measure=lambda text:sum(2 if ord(c)>127 else 1 for c in text)
        lines=ui.caption_lines('先补两株向日葵，再给下路补充豌豆射手。',measure,16)
        self.assertEqual(len(lines),2)
        self.assertTrue(lines[-1].endswith('…'))
        self.assertTrue(all(measure(line)<=16 for line in lines))
    def test_commentary_rejects_bad_text_before_saving(self):
        with patch.object(ui,'write_json') as write:
            for params in [{},{'plan':True},{'plan':'a'*97},{'action':'a'*161},{'plan':'bad\x00text'}]:
                with self.assertRaises(ValueError):ui.set_commentary(params,status())
            write.assert_not_called()
    def test_fullscreen_hud_is_inside_image_not_pillarboxes(self):
        layout=ui.viewport_layout(1920,1080)
        self.assertEqual(layout['viewport'],dict(x=240,y=0,width=1440,height=1080))
        self.assertEqual(layout['hud'],dict(x=262,y=1012,width=389,height=54))
        self.assertGreater(layout['hud']['x'],240)
    def test_windowed_geometry_keeps_existing_size_and_insets(self):
        layout=ui.viewport_layout(800,600)
        self.assertEqual(layout['hud'],dict(x=12,y=562,width=216,height=30))
    def test_ultrawide_portrait_and_high_resolution_remain_inside_image(self):
        for width,height in [(2560,1080),(1080,1920),(3840,2160),(1024,768)]:
            layout=ui.viewport_layout(width,height);v=layout['viewport'];h=layout['hud']
            self.assertGreaterEqual(h['x'],v['x'])
            self.assertGreaterEqual(h['y'],v['y'])
            self.assertLessEqual(h['x']+h['width'],v['x']+v['width'])
            self.assertLessEqual(h['y']+h['height'],v['y']+v['height'])
            self.assertLessEqual(abs(h['width']-216*layout['scale']),.5)
            self.assertLessEqual(abs(h['height']-30*layout['scale']),.5)
    def test_invalid_viewport_is_rejected(self):
        with self.assertRaises(ValueError):ui.viewport_layout(0,600)
    def test_decision_clock_survives_repeated_observations_and_plant_notes(self):
        clock=ui.HudClock()
        clock.update(status(),10)
        with patch.object(ui,'read_json',return_value=dict(text='new planting',session_id='test:123')):
            later=clock.update(status(),22)
        self.assertEqual(later['elapsed_seconds'],12)
        self.assertIn('00:12',later['title'])
        clock.update(status('advancing',remaining=100),23)
        self.assertEqual(clock.update(status(),25)['elapsed_seconds'],0)
        self.assertEqual(clock.update(status(),32)['elapsed_seconds'],7)
    def test_award_ignores_old_remaining_budget(self):
        p=ui.progress(status('award',remaining=42))
        self.assertEqual(p['phase'],'award')
        self.assertEqual(p['remaining_updates'],0)
        self.assertIsNone(p['next_decision_in_ms'])
    def test_live_eta_uses_observed_updates_and_speed_change_resets_samples(self):
        clock=ui.HudClock()
        clock.update(status('advancing',tick=100,remaining=900),0)
        p=clock.update(status('advancing',tick=200,remaining=800),.5)
        self.assertEqual(p['next_decision_in_ms'],4000)
        self.assertEqual(p['estimate_source'],'measured_live')
        p=clock.update(status('advancing',tick=220,remaining=780,speed=4),.6)
        self.assertNotEqual(p['estimate_source'],'measured_live')
    def test_native_pause_is_not_reported_as_running(self):
        p=ui.progress(status('native_pause',remaining=500))
        self.assertEqual(p['phase'],'native_pause')
        self.assertIsNone(p['next_decision_in_ms'])
    def test_invalid_speed_cannot_reach_controller(self):
        for value in [True,0,33,1.5,'2']:
            with self.assertRaises(ValueError):ui.validate(dict(speed=value))
    def test_live_speed_change_only_updates_controller_speed_word(self):
        ctl=Controller.__new__(Controller);ctl.identity=None;ctl.base=0x851000;ctl.M={"speed":36};writes=[]
        ctl.mem=type('Memory',(),dict(put=lambda _,a,n:writes.append((a,n))))()
        ctl.status=lambda:status('advancing',remaining=500,speed=4)
        with patch.object(ui,'write_json'),patch.object(ui,'ensure_overlay'):
            result=ctl.set_presentation(dict(speed=4))
        self.assertEqual(writes,[(ctl.base+ctl.M['speed'],4)])
        self.assertEqual(result['status']['bridge']['remaining'],500)

if __name__=='__main__':unittest.main(verbosity=2)
