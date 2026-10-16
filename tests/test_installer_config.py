"""Installer policy and unconfigured MCP regression checks; no game process access."""
import contextlib,json,unittest
from pathlib import Path
from unittest.mock import patch
from artifacts import Artifacts
import configure,pvz,runtime_bridge

class InstallerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(Artifacts().name)
        self.game=self.root/'unsupported.exe'
        self.game.write_bytes(b'MZ-not-a-supported-game')
    def test_policy_matches_runtime_whitelist(self):
        policy=json.loads((Path(runtime_bridge.__file__).parent/'compatibility.json').read_text(encoding='utf-8'))
        self.assertEqual({x['sha256'] for x in policy['supported']},{runtime_bridge.ORIGINAL_SHA256})
        self.assertNotIn(runtime_bridge.ORIGINAL_SHA256,{x['sha256'] for x in policy['unsupported']})
    def test_unsupported_configuration_rejected_without_opt_in(self):
        with patch.object(configure,'DATA',self.root),self.assertRaises(ValueError):
            configure.configure(str(self.game))
        self.assertFalse((self.root/'bridge-manifest.json').exists())
        self.assertEqual(self.game.read_bytes(),b'MZ-not-a-supported-game')
    def test_tools_install_without_enabling_unsupported_game(self):
        with patch.object(configure,'DATA',self.root):
            result=configure.configure(str(self.game),True)
        self.assertFalse(result['configured'])
        self.assertEqual(result['reason'],'UNSUPPORTED_GAME')
        self.assertFalse((self.root/'bridge-manifest.json').exists())
        self.assertTrue((self.root/'game-configuration.json').exists())
        self.assertEqual(self.game.read_bytes(),b'MZ-not-a-supported-game')
    def test_unsupported_selection_backs_up_previous_configuration(self):
        previous=b'{"game":"previously-selected-game.exe"}'
        (self.root/'bridge-manifest.json').write_bytes(previous)
        with patch.object(configure,'DATA',self.root):
            result=configure.configure(str(self.game),True)
        self.assertEqual(Path(result['previous_configuration_backup']).read_bytes(),previous)
        self.assertFalse((self.root/'bridge-manifest.json').exists())
    def test_unconfigured_status_is_a_normal_response(self):
        with patch.object(pvz,'DATA',self.root),patch.object(pvz,'exclusive',return_value=contextlib.nullcontext()):
            ctl=pvz.Controller()
            status=ctl.execute('status',{})
            self.assertFalse(status['running'])
            self.assertEqual(status['reason'],'GAME_NOT_CONFIGURED')
            response=pvz.response(ctl,dict(id=1,method='launch_game',params={}))
            self.assertEqual(response['error']['code'],'GAME_NOT_CONFIGURED')
            self.assertIn('modes',ctl.execute('list_modes',{}))

if __name__=='__main__':unittest.main()
