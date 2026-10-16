"""Save API regression fixtures: temp saves only, no live game access."""
import hashlib, struct, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import pvz

class Fixture(pvz.Controller):
    def __init__(self,path,scene=1,native=5):
        self.path=path;self.scene_code=scene;self.native=native;self.session='fixture'
        self.mem=self;self.base=0x10000;self.M={'admin_events':16};self.admin=0;self.writes=[]
    def status(self):return {'scene':{'code':self.scene_code,'board_present':False}}
    def loaded_profile_save(self):return 0x20000,3,self.path,self.path.read_bytes()
    def i32(self,address):return self.native
    def put(self,address,value):
        self.writes.append((address,value))
        if address==0x2004c:self.native=value
        else:self.admin=value
    def mailbox(self):return {'admin_events':self.admin}

class SaveTests(unittest.TestCase):
    def setUp(self):
        discard=Path(r'D:\Temp\Codex\待删除\save-api-tests');discard.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(dir=str(discard)));self.path=self.root/'user3.dat'
        data=bytearray(892);struct.pack_into('<II',data,0,12,5);self.path.write_bytes(data)
        self.original=bytes(data);self.ctl=Fixture(self.path)
        self.root_patch=patch.object(pvz,'DATA',self.root);self.root_patch.start()
        self.replay=patch.object(pvz.selection,'current',return_value=None);self.replay.start()
    def tearDown(self):
        self.replay.stop();self.root_patch.stop()
        # Keep generated fixture files together for user-managed cleanup.
        pass
    def params(self,level):return dict(level=level,expected_current_level=5,
        expected_save_sha256=hashlib.sha256(self.original).hexdigest())
    def test_read_only(self):
        info=self.ctl.get_save_info()
        self.assertTrue(info['can_edit_progress']);self.assertEqual(info['adventure_stage'],'1-5')
        self.assertEqual(info['save_sha256'],self.params(4)['expected_save_sha256'])
        self.assertEqual(self.ctl.writes,[]);self.assertEqual(self.path.read_bytes(),self.original)
    def test_award_not_editable(self):
        self.ctl.scene_code=5;self.assertFalse(self.ctl.get_save_info()['can_edit_progress'])
        with self.assertRaises(pvz.PvZError) as cm:self.ctl.set_adventure_progress(self.params(4))
        self.assertEqual(cm.exception.code,'MAIN_MENU_REQUIRED');self.assertEqual(self.ctl.writes,[])
    def test_mismatch_not_editable(self):
        self.ctl.native=4;self.assertFalse(self.ctl.get_save_info()['can_edit_progress'])
        with self.assertRaises(pvz.PvZError) as cm:self.ctl.set_adventure_progress(self.params(4))
        self.assertEqual(cm.exception.code,'PROGRESS_CHANGED');self.assertEqual(self.ctl.writes,[])
    def test_changed_hash_rejected(self):
        args=self.params(4);args['expected_save_sha256']='0'*64
        with self.assertRaises(pvz.PvZError) as cm:self.ctl.set_adventure_progress(args)
        self.assertEqual(cm.exception.code,'SAVE_CHANGED');self.assertEqual(self.ctl.writes,[])
    def test_noop_metadata(self):
        result=self.ctl.set_adventure_progress(self.params(5))
        self.assertFalse(result['changed']);self.assertFalse(result['admin_used'])
        self.assertEqual(result['profile_id'],3);self.assertIsNone(result['backup'])
        self.assertEqual(self.ctl.writes,[]);self.assertFalse((self.root/'backup').exists())
    def test_edit_preserves_other_bytes_and_backs_up(self):
        result=self.ctl.set_adventure_progress(self.params(4))
        self.assertEqual(Path(result['backup']).read_bytes(),self.original)
        actual=self.path.read_bytes();self.assertEqual(actual[:4],self.original[:4])
        self.assertEqual(actual[8:],self.original[8:]);self.assertEqual(struct.unpack_from('<I',actual,4)[0],4)
        self.assertEqual(self.ctl.native,4);self.assertEqual(self.ctl.admin,1)
        self.assertEqual(result['changed_save_byte_offsets'],[4])

if __name__=='__main__':unittest.main()
