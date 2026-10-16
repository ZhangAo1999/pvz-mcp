"""Local Win32 PvZ controller. JSON lines for MCP, or CLI: pvz.py METHOD JSON.
No network listener, remote thread, or process suspension. Native commands run
on the game's App/Board update thread through the version-pinned mailbox patch.
"""
import ctypes as C
from ctypes import wintypes as W
import argparse, contextlib, hashlib, json, os, struct, sys, time, uuid, subprocess
from pathlib import Path
import msvcrt
import presentation as spectator
import selection

from paths import APP, DATA
ROOT=APP
class PvZError(Exception):
    def __init__(self,code,message,details=None):
        super().__init__(message); self.code=code; self.details=details
def fail(code,message,details=None):raise PvZError(code,message,details)

k=C.WinDLL('kernel32',use_last_error=True);p=C.WinDLL('psapi',use_last_error=True)
k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)];k.QueryFullProcessImageNameW.restype=W.BOOL
k.ReadProcessMemory.argtypes=[W.HANDLE,W.LPCVOID,W.LPVOID,C.c_size_t,C.POINTER(C.c_size_t)];k.ReadProcessMemory.restype=W.BOOL
k.WriteProcessMemory.argtypes=[W.HANDLE,W.LPVOID,W.LPCVOID,C.c_size_t,C.POINTER(C.c_size_t)];k.WriteProcessMemory.restype=W.BOOL
k.GetProcessTimes.argtypes=[W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)];k.GetProcessTimes.restype=W.BOOL
p.EnumProcesses.argtypes=[C.POINTER(W.DWORD),W.DWORD,C.POINTER(W.DWORD)];p.EnumProcesses.restype=W.BOOL
k.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];k.GetExitCodeProcess.restype=W.BOOL

class Memory:
    def __init__(self,handle):self.handle=handle
    def read(self,address,size):
        if not isinstance(address,int) or address<0x10000 or address+size>0x80000000 or not 0<=size<=8*1024*1024:
            fail('INVALID_READ','Invalid bounded game memory read',{'address':hex(address),'size':size})
        buf=C.create_string_buffer(size);n=C.c_size_t()
        if not k.ReadProcessMemory(self.handle,address,buf,size,C.byref(n)) or n.value!=size:
            fail('READ_FAILED','Game memory is unavailable; the scene or process may have changed',{'address':hex(address),'winerror':C.get_last_error()})
        return buf.raw
    def u32(self,a):return struct.unpack('<I',self.read(a,4))[0]
    def i32(self,a):return struct.unpack('<i',self.read(a,4))[0]
    def write(self,a,data):
        n=C.c_size_t();b=C.create_string_buffer(data)
        if not k.WriteProcessMemory(self.handle,a,b,len(data),C.byref(n)) or n.value!=len(data):
            fail('WRITE_FAILED','Mailbox write failed',{'winerror':C.get_last_error()})
    def put(self,a,v):self.write(a,struct.pack('<I',v&0xffffffff))

@contextlib.contextmanager
def exclusive():
    """Serializes commands across CLI processes and different AI/MCP clients."""
    f=open(DATA/'controller.lock','a+b');f.seek(0,2)
    if not f.tell():f.write(b'0');f.flush()
    deadline=time.monotonic()+5
    while True:
        try:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);break
        except OSError:
            if time.monotonic()>deadline:f.close();fail('BUSY','Another controller owns the game command lock')
            time.sleep(.02)
    try:yield
    finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1);f.close()

def integer(params,key,lo,hi,default=None):
    value=params.get(key,default)
    if type(value) is not int or not lo<=value<=hi:fail('INVALID_ARGUMENT',key+' must be an integer in [%d, %d]'%(lo,hi))
    return value

class Controller:
    def __init__(self):
        config=DATA/'bridge-manifest.json'
        self.manifest=json.loads(config.read_text(encoding='utf-8')) if config.exists() else None
        self.path=os.path.normcase(os.path.abspath(self.manifest['game'])) if self.manifest else None
        self.base=self.manifest['data_va'] if self.manifest else None
        self.M=self.manifest['mailbox'] if self.manifest else {}
        self.identity=None
    def launch(self):
        try:
            self.connect()
            try:return dict(launched=False,status=self.status())
            finally:self.close()
        except PvZError as ex:
            if ex.code!='NOT_RUNNING':raise
        task_temp=DATA/'temp';task_temp.mkdir(parents=True,exist_ok=True)
        env=dict(os.environ,TEMP=str(task_temp),TMP=str(task_temp))
        from runtime_bridge import launch
        launch(self.manifest['game'],self.manifest)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                self.connect()
                try:
                    if self.mailbox()['app_heartbeat']>0:return dict(launched=True,status=self.status())
                finally:self.close()
            except PvZError as ex:
                if ex.code not in ['NOT_RUNNING','READ_FAILED','UNSTABLE_STATUS']:raise
            time.sleep(.05)
        fail('LAUNCH_TIMEOUT','Game launch was requested, but the process is not ready')
    def connect(self):
        if not self.manifest:
            fail('GAME_NOT_CONFIGURED','The tools are installed, but no supported game is configured. Run the installer again and select a supported EXE.')
        if hashlib.sha256(Path(self.path).read_bytes()).hexdigest()!=self.manifest['sha256']:
            fail('BUILD_MISMATCH','The game executable differs from this controller build')
        ids=(W.DWORD*4096)();needed=W.DWORD()
        if not p.EnumProcesses(ids,C.sizeof(ids),C.byref(needed)):fail('ENUM_FAILED','Cannot enumerate game process')
        matches=[]
        for pid in ids[:needed.value//4]:
            h=k.OpenProcess(0x1000,False,pid)
            if not h:continue
            try:
                buf=C.create_unicode_buffer(32768);n=W.DWORD(len(buf))
                if k.QueryFullProcessImageNameW(h,0,buf,C.byref(n)) and os.path.normcase(buf.value)==self.path:matches.append(pid)
            finally:k.CloseHandle(h)
        if len(matches)!=1:fail('NOT_RUNNING' if not matches else 'AMBIGUOUS_PROCESS','Start exactly one copy of '+self.manifest['game'],{'pids':matches})
        self.pid=matches[0];self.handle=k.OpenProcess(0x438,False,self.pid)
        if not self.handle:fail('OPEN_FAILED','Cannot access the selected game',{'winerror':C.get_last_error()})
        self.mem=Memory(self.handle)
        times=[W.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(self.handle,*[C.byref(t) for t in times]):self.close();fail('IDENTITY_FAILED','Cannot identify game process')
        self.session='%d:%d'%(self.pid,(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        try:
            if self.mem.read(self.base,8)!=b'PZCT'+struct.pack('<I',self.manifest['version']):fail('BRIDGE_MISMATCH','The running game has no matching controller bridge. Launch the original game using pvz_launch_game so the runtime bridge is loaded.')
            from bridge_asm import assemble
            code=assemble()[0]
            from collect_extension import verify
            try:verify(self,code,self.mem.read(self.manifest['code_va'],len(code)))
            except ValueError as ex:fail('BRIDGE_MISMATCH',str(ex))
            spectator.optional('identity',spectator.identity,self)
        except Exception:self.close();raise
    def close(self):
        if getattr(self,'handle',None):k.CloseHandle(self.handle);self.handle=None
    def mailbox(self):
        block=self.mem.read(self.base,128)
        return {key:struct.unpack_from('<I',block,off)[0] for key,off in self.M.items()}
    def board(self):
        mb=self.mailbox();b=mb['board']
        if b<0x10000:fail('NO_BOARD','Open an adventure level in the game first',{'bridge':mb})
        app=mb['app']
        if self.mem.u32(app+0x868)!=b:fail('BOARD_CHANGED','The board was closed; open a level and read state again')
        return b,mb
    def wait(self,predicate,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            mb=self.mailbox()
            if predicate(mb):return mb
            time.sleep(.005)
        fail('TIMEOUT','Game did not acknowledge the command. Bring its window to the foreground and close any game menu; do not blindly retry a placement.',{'mailbox':self.mailbox()})
    def command(self,cmd,arg0=0,arg1=0,arg2=0,epoch=0):
        if cmd>=10:mb=self.mailbox()
        else:b,mb=self.board()
        if mb['request']!=mb['ack']:fail('PENDING_COMMAND','Previous command is still pending; do not overwrite it',{'mailbox':mb})
        seq=(mb['request']+1)&0xffffffff or 1
        # Publish complete arguments before the final aligned 32-bit request word.
        for name,val in [('command',cmd),('arg0',arg0),('arg1',arg1),('arg2',arg2),('expected_epoch',epoch)]:self.mem.put(self.base+self.M[name],val)
        self.mem.put(self.base+self.M['request'],seq)
        mb=self.wait(lambda m:m['ack']==seq and m['busy']==0 and not m.get('app_busy',0))
        errors={1:'UNKNOWN_COMMAND',2:'STALE_BOARD_EPOCH',3:'PAUSE_REQUIRED',4:'NOT_PLAYING',5:'INVALID_ARGUMENT',6:'ILLEGAL_GRID',7:'CARD_UNAVAILABLE',8:'UNSUPPORTED_MODE',9:'CURSOR_BUSY',10:'CARD_SELECTION_FAILED',11:'LOADING',12:'MENU_REQUIRED',13:'NO_PROFILE',14:'NOT_CHOOSING_SEEDS',15:'CHOOSER_BUSY',16:'WRONG_DECK_SIZE',17:'FIXED_SEEDS',18:'SEED_UNAVAILABLE',20:'STALE_ENTITY',21:'UNSUPPORTED_PICKUP'}
        if mb['result']:fail(errors.get(mb['result'],'NATIVE_ERROR'),'Game rejected the command',{'native_result':mb['result'],'native_reply':mb['reply'],'board_epoch':mb['epoch'],'request':seq})
        return mb
    def pause(self):return self.command(1)
    def state(self):
        from state import snapshot,StateError
        for _ in range(12):
            try:b,start=self.board()
            except PvZError as ex:
                if ex.code not in ['NO_BOARD','BOARD_CHANGED']:raise
                current=self.status()
                return dict(board=None,board_epoch=None,session_id=self.session,scene=current['scene'],status=current)
            if start['request']!=start['ack']:
                fail('PENDING_COMMAND','A command is queued; its outcome must be acknowledged before returning state',{'mailbox':start})
            if self.mem.i32(start['app']+0x920)==2:
                current=self.status();chooser=self.mem.u32(start['app']+0x874)
                return dict(board_epoch=start['epoch'],session_id=self.session,scene=2,level=self.mem.i32(b+0x5568),
                    seed_selection=self.seed_choices() if chooser and self.mem.read(chooser+0x65,1)[0] else None,
                    status=current,preparation_running=True,
                    note='Native intro is running. Gameplay will pause automatically on its first playing boundary.')
            if start.get('app_busy',0):time.sleep(.001);continue
            if start['paused']!=1 or start['remaining'] or start['busy']:self.pause();b,start=self.board()
            if start.get('app_busy',0):continue
            try:result=snapshot(self.mem,b,start['epoch'])
            except StateError:
                if self.mailbox().get('app_heartbeat')!=start.get('app_heartbeat'):continue
                raise
            end=self.mailbox()
            if (end['board'],end['epoch'],end['completed'],end['request'],end['ack'],end['busy'],end['paused'],end['remaining'],end.get('app_busy',0),end.get('app_heartbeat'))==(b,start['epoch'],start['completed'],start['request'],start['request'],0,1,0,0,start.get('app_heartbeat')):
                result.update(session_id=self.session,board_epoch=start['epoch'],game_build=self.manifest['sha256'],
                    controller={key:end[key] for key in ['paused','remaining','speed','completed','advanced','stop','admin_events']},
                    consistency='paused Board::Update; unchanged App update heartbeat; command lock held',
                    coordinate_system='rows, columns and card slots are 1-based')
                app=start['app'];chooser=self.mem.u32(app+0x874)
                if chooser and self.mem.read(chooser+0x65,1)[0]:result['seed_selection']=self.seed_choices()
                return result
        fail('UNSTABLE_SNAPSHOT','Game changed while reading; request a fresh snapshot')
    def status(self):
        last=None
        for _ in range(12):
            before=self.mailbox()
            if before.get('app_busy',0):time.sleep(.001);continue
            try:last=self._status_unchecked()
            except PvZError as ex:
                if ex.code=='READ_FAILED' and self.mailbox().get('app_heartbeat')!=before.get('app_heartbeat'):continue
                raise
            after=self.mailbox()
            if not after.get('app_busy',0) and after.get('app_heartbeat')==before.get('app_heartbeat'):
                last['status_consistency']='unchanged App update heartbeat'
                last['presentation']=spectator.progress(last)
                return last
        fail('UNSTABLE_STATUS','App changed while reading status; request again')
    def _status_unchecked(self):
        mb=self.mailbox()
        app=self.mem.u32(0x755e0c)
        scene=None;mode=None;profile=None;board=None;screen_pointers={}
        if app>=0x10000:
            scene=self.mem.i32(app+0x920);mode=self.mem.i32(app+0x91c);profile=self.mem.u32(app+0x950);board=self.mem.u32(app+0x868)
            screen_pointers={name:bool(self.mem.u32(app+off)) for name,off in [('title',0x86c),('main_menu',0x870),('seed_chooser',0x874),('award',0x878),('challenge_menu',0x880)]}
        scenes={0:'title_or_loading',1:'main_menu',2:'level_preparation',3:'playing',4:'zombies_won',5:'award',6:'credits',7:'challenge_menu'}
        current_board=bool(board and board==mb['board'])
        scene_info=dict(code=scene,name=scenes.get(scene,'initializing' if scene is None else 'unknown'),mode_id=mode,
            profile_loaded=bool(profile),board_present=bool(board),board_attached=current_board,screens=screen_pointers)
        if app>=0x10000:
            title=self.mem.u32(app+0x86c);chooser=self.mem.u32(app+0x874)
            scene_info.update(loading_complete=bool(self.mem.read(app+0x56a,1)[0]),
                title_ready=bool(title and self.mem.read(title+0xb9,1)[0]),
                choosing_seeds=bool(chooser and self.mem.read(chooser+0x65,1)[0]))
            if profile:scene_info['profile_adventure_level']=self.mem.i32(profile+0x4c)
        if current_board:
            scene_info.update(level=self.mem.i32(board+0x5568),board_epoch=mb['epoch'],game_pause_flag=bool(self.mem.read(board+0x17c,1)[0]))
            if mode==0 and 1<=scene_info['level']<=50:
                n=scene_info['level'];scene_info['adventure_stage']='%d-%d'%((n-1)//10+1,(n-1)%10+1)
        if scene_info.get('profile_adventure_level'):
            n=scene_info['profile_adventure_level'];scene_info['profile_adventure_stage']='%d-%d'%((n-1)//10+1,(n-1)%10+1)
        replay=selection.current(self)
        if replay:scene_info['replay_context']=dict(selected_level=replay['selected_level'],retained_progress_level=replay['original_level'])
        return dict(running=True,pid=self.pid,session_id=self.session,game=self.manifest['game'],game_build=self.manifest['sha256'],bridge=mb,
            scene=scene_info,capabilities=['launch','menu_status','native_mode_start','adventure_current_progress_by_default',
                'explicit_adventure_level_selection','paused_json','bounded_advance','native_rule_planting_adventure',
                'native_rule_planting_zombotany','seed_selection_adventure_zombotany','normal_pickup_sun_money_shovel',
                'spectator_hud','spectator_commentary','asynchronous_bounded_advance','adjustable_speed_while_advancing','admin_sun','speed_1_to_32'],replay=False)
    def modes(self):
        catalog=json.loads((ROOT/'modes.json').read_text(encoding='utf-8'))
        verified={}
        navigation=ROOT/'evidence'/'live-navigation-verification.json'
        if self.manifest and navigation.exists():
            report=json.loads(navigation.read_text(encoding='utf-8'))
            for response in report.get('responses',[])+[report.get('restored',{})]:
                item=response.get('result',{})
                if item.get('started') and item.get('status',{}).get('game_build')==self.manifest['sha256']:
                    verified[item['mode_id']]=item['phase']
        mcp_actions=ROOT/'evidence'/'mcp-live-actions.json'
        if self.manifest and mcp_actions.exists():
            try:
                report=json.loads(mcp_actions.read_text(encoding='utf-8'))
                if report.get('passed') and report.get('expected_game_build')==self.manifest['sha256']:
                    for action in report.get('calls',[]):
                        value=action.get('response',{}).get('structuredContent',{})
                        status=value.get('status') or {}
                        if status.get('game_build')==self.manifest['sha256']:
                            scene=status.get('scene',{})
                            if scene.get('board_present'):
                                verified[scene['mode_id']]='playing' if scene.get('code')==3 else value.get('phase','preparation')
            except (OSError,ValueError):pass # Optional test report may still be being written.
        for mode in catalog['modes']:
            mode['start_supported']=mode['id'] not in [43,50,71,72]
            mode['placement_supported']=mode['id'] in [0,16]
            mode['seed_selection_supported']=mode['id'] in [0,16]
            mode['start_validated']=mode['id'] in verified
            mode['runtime_start_phase']=verified.get(mode['id'])
            mode['playing_phase_verified']=verified.get(mode['id'])=='playing'
        return dict(modes=catalog['modes'],notes=['Names and IDs are verified against this game build.',
            'Direct mode start uses the native game factory; it does not change saved unlock flags.',
            'Planting and deck selection tools support adventure and Zombotany. Mode-specific controls are separate.',
            'Zen Garden, Tree of Wisdom, and promotional modes are not supported for direct start.'])
    def mode_id(self,params):
        mode=params.get('mode','adventure')
        if not isinstance(mode,str):fail('INVALID_ARGUMENT','mode must be a catalog key or decimal ID string')
        for record in self.modes()['modes']:
            if mode.lower() in [str(record['id']),record['key'].lower(),record['name'].lower()]:
                if not record['start_supported']:fail('UNSUPPORTED_MODE','This mode needs additional initialization',record)
                return record['id']
        fail('INVALID_MODE','Use list_modes to select an exact mode key or ID')
    def enter_menu(self):
        status=self.status()
        if status['scene']['code']==1:
            selection.finish_if_left(self)
            return self.status()
        if not status['scene'].get('loading_complete') or (status['scene']['screens'].get('title') and not status['scene'].get('title_ready')):
            fail('LOADING','Resources are still loading; check status again')
        if selection.current(self):
            if status['scene']['board_attached']:self.pause()
            selection.restore(self)
        self.command(10)
        return self.status()
    def start_level(self,params):
        mode=self.mode_id(params)
        level=integer(params,'level',1,50) if 'level' in params else 0
        if mode!=0 and level:fail('INVALID_ARGUMENT','level is for adventure; puzzle stages have distinct mode IDs')
        force=params.get('force_new',False)
        if type(force) is not bool:fail('INVALID_ARGUMENT','force_new must be boolean')
        before=self.status()
        if before['scene']['board_present'] and not force:
            if mode==0 and not level and before['scene']['mode_id']==0:
                final=self.state()
                return dict(started=False,already_open=True,level_source='existing_board',status=self.status(),state=final,
                    note='The current adventure board was preserved. Use advance or resume to continue.')
            fail('LEVEL_ALREADY_OPEN','A board is already open. Use force_new=true to replace this in-memory game.',{'status':before})
        if before['scene']['screens'].get('title'):self.enter_menu()
        if not self.status()['scene'].get('loading_complete'):fail('LOADING','Wait for resources to load')
        if selection.current(self):
            self.enter_menu()
            before=self.status()
        if mode==0 and level:selection.begin(self,level)
        self.command(11,mode,level)
        end=time.monotonic()+15
        while time.monotonic()<end:
            current=self.status()
            if current['scene']['code']==3 or current['scene'].get('choosing_seeds'):break
            time.sleep(.02)
        current=self.status()
        state=self.state() if current['scene']['board_attached'] else None
        current=self.status()
        return dict(started=True,mode_id=mode,requested_level=level or None,
            level_source='explicit_selection' if level else 'profile_progress' if mode==0 else 'native_mode',
            profile_level_before=before['scene'].get('profile_adventure_level'),
            phase='choosing_seeds' if current['scene'].get('choosing_seeds') else current['scene']['name'],status=current,state=state)
    def seed_choices(self):
        from state import definitions
        self.command(13)
        names={entry['type']:entry['name'] for entry in definitions()['plants']}
        for _ in range(12):
            before=self.mailbox()
            if before.get('app_busy',0):time.sleep(.001);continue
            app=before['app'];chooser=self.mem.u32(app+0x874)
            if not chooser or not self.mem.read(chooser+0x65,1)[0]:return None
            if not self.mem.u32(app+0x950):fail('NO_PROFILE','Seed selection needs a loaded profile')
            raw=self.mem.read(chooser,0xd54);b=self.mem.u32(app+0x868)
            if struct.unpack_from('<I',raw,0xd2c)[0]!=b:fail('BOARD_CHANGED','Seed chooser no longer belongs to this board')
            bank=self.mem.u32(b+0x15c)
            required=self.mem.i32(bank+0x24)
            choices=[]
            for type_id in range(48):
                off=0xbc+type_id*0x3c
                state,slot=struct.unpack_from('<ii',raw,off+0x24)
                unlocked=bool(self.mem.u32(self.base+192+type_id*4))
                choices.append(dict(type=type_id,name=names.get(type_id),unlocked=unlocked,state=state,
                    selected=state==1,slot=slot+1 if state==1 else None,fixed=bool(raw[off+0x38]),
                    candidate=state==3))
            after=self.mailbox()
            if after.get('app_busy',0) or after.get('app_heartbeat')!=before.get('app_heartbeat'):continue
            selected=sorted((s for s in choices if s['selected']),key=lambda s:s['slot'])
            available=[s for s in choices if s['unlocked'] and s['state'] in [1,3]]
            return dict(required_count=required,selected=selected,available_seeds=available,
                flight_count=struct.unpack_from('<i',raw,0xd38)[0],selected_count=struct.unpack_from('<i',raw,0xd3c)[0],
                view_lawn=struct.unpack_from('<i',raw,0xd50)[0],supported_mode=self.mem.i32(app+0x91c) in [0,16],
                notes=['Native unlock and trial conditions are checked again when applying a deck.','Imitator and replacement of fixed decks are not supported.'])
        fail('UNSTABLE_CHOOSER','Seed chooser changed during read; request fresh state')
    def select_seeds(self,params):
        b,mb=self.board();epoch=self.check_epoch(params,mb,True)
        seeds=params.get('seeds')
        if not isinstance(seeds,list) or not 1<=len(seeds)<=10 or any(type(s) is not int or not 0<=s<=47 for s in seeds) or len(set(seeds))!=len(seeds):
            fail('INVALID_ARGUMENT','seeds must contain 1..10 unique integer types from 0..47')
        self.pause();before=self.seed_choices()
        if before is None:fail('NOT_CHOOSING_SEEDS','No visible seed chooser')
        if not before['supported_mode']:fail('UNSUPPORTED_MODE','Deck selection is verified for adventure and Zombotany')
        if len(seeds)!=before['required_count']:fail('WRONG_DECK_SIZE','Choose exactly the required number of seeds',before)
        available={s['type'] for s in before['available_seeds']}
        if not set(seeds)<=available:fail('SEED_UNAVAILABLE','Some requested seeds are locked or disabled',{'requested':seeds,'choices':before})
        if any(s['fixed'] for s in before['selected']):fail('FIXED_SEEDS','This deck contains fixed native cards; replacing it is not supported',before)
        self.mem.write(self.base+128,struct.pack('<'+'I'*len(seeds),*seeds))
        self.command(12,len(seeds),epoch=epoch)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            current=self.status()
            if current['scene']['code']!=2:break
            time.sleep(.02)
        warnings=[]
        if 1 not in seeds and 9 not in seeds:warnings.append('No sunflower or sun-shroom selected')
        final=self.state()
        return dict(selected_seeds=seeds,warnings=warnings,status=self.status(),state=final)
    def check_epoch(self,params,mb,required=False):
        epoch=integer(params,'expected_board_epoch',1,0xffffffff) if required or 'expected_board_epoch' in params else mb['epoch']
        if epoch!=mb['epoch']:fail('STALE_BOARD_EPOCH','Read state again before acting',{'expected':epoch,'actual':mb['epoch']})
        return epoch
    def set_presentation(self,params,connected=True):
        try:cfg=spectator.validate(params)
        except ValueError as ex:fail('INVALID_ARGUMENT',str(ex))
        if connected and 'speed' in params:
            # One aligned controller setting, not a gameplay/resource field.
            # A running batch retains its remaining budget; new speed is read
            # by the bridge at the next outer Board update boundary.
            self.mem.put(self.base+self.M['speed'],cfg['speed'])
        spectator.write_json(DATA/'presentation.json',cfg)
        spectator.ensure_overlay()
        return dict(settings=cfg,game_connected=connected,
            applies='next outer update, preserving remaining budget' if connected else 'next game connection',
            status=self.status() if connected else None)
    def prepare_advance(self):
        spectator.hold_for_spectator(self.session)
        return spectator.calibrate(self)
    def loaded_profile_save(self):
        app=self.mailbox()['app'];profile=self.mem.u32(app+0x950)
        if not profile:fail('NO_PROFILE','Load a native player profile first')
        profile_id=self.mem.u32(profile+0x20)
        if not 1<=profile_id<=999999:fail('PROFILE_ID_MISMATCH','Unsupported native profile identity')
        path=Path(r'C:\ProgramData\PopCap Games\PlantsVsZombies\userdata')/('user%d.dat'%profile_id)
        data=path.read_bytes()
        if len(data)!=892 or struct.unpack_from('<I',data)[0]!=12:
            fail('SAVE_FORMAT_MISMATCH','The current native profile has an unsupported save layout')
        return profile,profile_id,path,data
    def get_save_info(self):
        status=self.status()
        profile,profile_id,path,data=self.loaded_profile_save()
        saved_level=struct.unpack_from('<I',data,4)[0]
        native_level=self.mem.i32(profile+0x4c)
        replay=bool(selection.current(self))
        editable=(status['scene']['code']==1 and not status['scene']['board_present']
            and not replay and native_level==saved_level and 1<=saved_level<=50)
        return dict(session_id=self.session,profile_id=profile_id,save_path=str(path),
            save_version=12,save_bytes=len(data),save_sha256=hashlib.sha256(data).hexdigest(),
            saved_level=saved_level,native_level=native_level,
            adventure_stage=('%d-%d'%((saved_level-1)//10+1,(saved_level-1)%10+1)) if 1<=saved_level<=50 else None,
            can_edit_progress=editable,active_replay=replay,
            editable_fields=['adventure_level'],backup_directory=str(DATA/'backup'),
            scene=status['scene'])
    def set_adventure_progress(self,params):
        level=integer(params,'level',1,50)
        expected=integer(params,'expected_current_level',1,50)
        status=self.status()
        if status['scene']['code']!=1 or status['scene']['board_present']:
            fail('MAIN_MENU_REQUIRED','Change saved adventure progress only at the main menu without a board')
        if selection.current(self):fail('ACTIVE_REPLAY','Finish or leave the temporary replay first')
        profile,profile_id,path,data=self.loaded_profile_save()
        digest=hashlib.sha256(data).hexdigest()
        if digest!=params.get('expected_save_sha256'):
            fail('SAVE_CHANGED','Read and verify the current save before changing progress',{'sha256':digest})
        native_level=self.mem.i32(profile+0x4c);saved_level=struct.unpack_from('<I',data,4)[0]
        if native_level!=expected or saved_level!=expected:
            fail('PROGRESS_CHANGED','Memory and saved progress must match the expected current level',
                {'native_level':native_level,'saved_level':saved_level})
        if level==expected:return dict(changed=False,admin_used=False,level=level,profile_id=profile_id,
            backup=None,save_sha256=digest,changed_save_byte_offsets=[],status=status)
        backup=DATA/'backup'/('progress-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True,exist_ok=False)
        (backup/path.name).write_bytes(data)
        receipt=dict(session_id=self.session,profile_id=profile_id,path=str(path),backup=str(backup/path.name),
            before_level=expected,after_level=level,before_sha256=digest,phase='prepared',time=time.time())
        spectator.write_json(backup/'receipt.json',receipt)
        updated=bytearray(data);struct.pack_into('<I',updated,4,level)
        self.mem.put(profile+0x4c,level)
        try:
            with path.open('r+b') as f:
                if hashlib.sha256(f.read()).hexdigest()!=digest:fail('SAVE_CHANGED','Save changed immediately before write')
                f.seek(4);f.write(updated[4:8]);f.flush();os.fsync(f.fileno())
            actual=path.read_bytes()
            if actual!=updated or self.mem.i32(profile+0x4c)!=level:
                fail('PROGRESS_VERIFY_FAILED','Saved progress did not match the requested update')
        except Exception:
            self.mem.put(profile+0x4c,native_level)
            raise
        mb=self.mailbox();self.mem.put(self.base+self.M['admin_events'],mb['admin_events']+1)
        receipt.update(phase='complete',after_sha256=hashlib.sha256(actual).hexdigest())
        spectator.write_json(backup/'receipt.json',receipt)
        return dict(changed=True,admin_used=True,level=level,profile_id=profile_id,backup=str(backup/path.name),
            save_sha256=receipt['after_sha256'],changed_save_byte_offsets=[i for i,(a,b) in enumerate(zip(data,actual))if a!=b],
            status=self.status())
    def dispatch(self,method,params):
        if method=='status':return self.status()
        if method=='get_save_info':return self.get_save_info()
        if method=='admin_set_progress':return self.set_adventure_progress(params)
        if method=='set_presentation':return self.set_presentation(params)
        if method=='set_commentary':
            status=self.status()
            try:note=spectator.set_commentary(params,status)
            except ValueError as ex:fail('INVALID_ARGUMENT',str(ex))
            return dict(commentary=note,status=status,gameplay_changed=False)
        if method=='return_to_menu':return self.enter_menu()
        if method=='start_game':return self.start_level(params)
        if method=='select_seeds':return self.select_seeds(params)
        if method=='get_state':return self.state()
        if method=='pause':
            if self.status()['scene']['board_attached']:self.pause()
            return self.state()
        b,mb=self.board();epoch=self.check_epoch(params,mb,method in ['can_place','place_plant','admin_set_sun','collect'])
        if method=='collect':
            current=self.state();coin_id=params.get('coin_id')
            coin=next((x for x in current.get('coins',[])if x['id']==coin_id),None)
            if coin is None:fail('STALE_ENTITY','Pickup no longer exists; read state again')
            if coin['type'] not in [1,2,3,4,5,6,9]:fail('UNSUPPORTED_PICKUP','Only normal money/sun pickups and the shovel reward are currently supported')
            if coin['collecting']:return dict(collected=False,already_collecting=True,state=current)
            from collect_extension import install
            install(self);self.command(14,coin['handle'],epoch=epoch)
            after=self.state()
            return dict(collected=True,coin_id=coin_id,pickup_type=coin['type'],sun_before=current['sun'],state=after)
        if method=='resume':
            self.command(2,0,integer(params,'speed',1,32,spectator.settings()['speed']),epoch=epoch)
            return {'running':True,'speed':self.mailbox()['speed'],'board_epoch':epoch}
        if method=='advance':
            try:ticks,game_ms=spectator.advance_request(params)
            except ValueError as ex:fail('INVALID_ARGUMENT',str(ex))
            speed=integer(params,'speed',1,32,spectator.settings()['speed'])
            wait=params.get('wait',True)
            if type(wait) is not bool:fail('INVALID_ARGUMENT','wait must be boolean')
            began=time.monotonic();self.pause();b,start=self.board();self.check_epoch(params,start)
            if self.mem.i32(start['app']+0x920)!=3:fail('NOT_PLAYING','Begin the level before advancing simulation')
            interval,timing_source=self.prepare_advance()
            b,start=self.board();self.check_epoch(params,start)
            start_tick=self.mem.i32(b+0x5584)
            self.command(3,ticks,speed,epoch=epoch)
            if not wait:
                current=self.status();p=current.get('presentation',{})
                estimate=p.get('next_decision_in_ms')
                if estimate is None:estimate=int(ticks*interval/speed)
                return dict(started=True,asynchronous=True,requested_updates=ticks,requested_game_ms=game_ms,speed=speed,
                    estimated_wait_ms=estimate,estimate_source=timing_source,
                    session_id=self.session,board_epoch=epoch,status=current,
                    next_step='Sleep for the estimate, then call status. Read get_state when phase=decision; get_state called early pauses the remaining budget.')
            def finished(m):
                if m['busy'] or m.get('app_busy',0):return False
                if not m['remaining']:return True
                # EndLevel may destroy the Board in App::Update, after the last
                # Board hook returned. Its mailbox then retains unused budget.
                app=m['app']
                return self.mem.u32(app+0x868)!=b or self.mem.i32(app+0x920)!=3
            wall_limit=False
            try:done=self.wait(finished,timeout=max(.1,40-(time.monotonic()-began)))
            except PvZError as ex:
                if ex.code!='TIMEOUT':raise
                # Recheck before pausing: a transition may coincide with timeout.
                done=self.mailbox()
                if self.mem.u32(done['app']+0x868)==b:
                    try:self.pause()
                    except PvZError as changed:
                        if changed.code not in ['NO_BOARD','BOARD_CHANGED']:raise
                    done=self.mailbox();wall_limit=True
            reason={1:'budget_exhausted',2:'scene_changed',3:'native_game_pause',4:'board_changed'}.get(done['stop'],'unknown')
            if wall_limit:reason='wall_time_limit'
            final=None;delta=None
            try:
                now_board,now=self.board()
                if now_board==b and now['epoch']==epoch:
                    final=self.state();delta=final['counters']['board_updates']-start_tick
                else:reason='board_changed'
            except PvZError as ex:
                if ex.code not in ['NO_BOARD','BOARD_CHANGED']:raise
                reason='board_changed'
            actual_updates=(done['total_updates']-start['total_updates'])&0xffffffff
            return dict(requested_updates=ticks,requested_game_ms=game_ms,advanced_updates=actual_updates,board_update_delta=delta,stop_reason=reason,state=final,status=self.status() if final is None else None)
        if method in ['can_place','place_plant']:
            self.pause();b,now=self.board();self.check_epoch(params,now,True);current=self.state()
            if current['mode'] not in [0,16]:fail('UNSUPPORTED_MODE','Normal planting supports adventure and Zombotany')
            rows=6 if self.mem.i32(b+0x5564) in [2,3] else 5
            if self.mem.i32(b+0x5564) not in range(6):fail('UNSUPPORTED_BACKGROUND','This background has not been verified')
            row=integer(params,'row',1,rows);col=integer(params,'col',1,9);slot=integer(params,'card_slot',1,len(current['cards']))
            if method=='can_place':
                try:self.command(4,slot-1,row-1,col-1,epoch);return dict(can_place=True,board_epoch=epoch,card_slot=slot,row=row,col=col)
                except PvZError as ex:
                    if ex.code in ['ILLEGAL_GRID','CARD_UNAVAILABLE']:return dict(can_place=False,reason=ex.code,details=ex.details)
                    raise
            old={item['id'] for item in current['plants']}
            self.command(5,slot-1,row-1,col-1,epoch)
            after=self.state();created=[item for item in after['plants'] if item['id'] not in old]
            if not created:fail('NATIVE_PLACEMENT_FAILED','Native input returned without a new plant; inspect current state before retrying',{'state':after})
            return dict(placed=True,created=created,sun_before=current['sun'],sun_after=after['sun'],state=after)
        if method=='admin_set_sun':
            amount=integer(params,'amount',0,9990);self.pause();self.command(6,amount,epoch=epoch)
            return dict(admin_used=True,state=self.state())
        fail('UNKNOWN_METHOD','Unknown tool method '+method)
    def execute(self,method,params):
        if not isinstance(params,dict):fail('INVALID_ARGUMENT','params must be an object')
        with exclusive():
            if method=='launch_game':
                result=self.launch();spectator.optional('overlay',spectator.ensure_overlay);return result
            if method=='list_modes':return self.modes()
            try:self.connect()
            except PvZError as ex:
                if method=='status' and ex.code in ['NOT_RUNNING','NO_BOARD','GAME_NOT_CONFIGURED']:return dict(running=False,reason=ex.code,message=str(ex))
                if method=='set_presentation' and ex.code in ['NOT_RUNNING','GAME_NOT_CONFIGURED'] and 'expected_session_id' not in params:
                    return self.set_presentation(params,False)
                raise
            try:
                # A persistent MCP connection must reobserve after process restart.
                if 'expected_session_id' in params and params['expected_session_id']!=self.session:
                    fail('STALE_SESSION','The game process restarted; read fresh state',{'session_id':self.session})
                if self.identity and self.identity!=self.session and method not in ['status','get_state','pause','start_game','return_to_menu']:
                    fail('SESSION_CHANGED','Game restarted; call get_state before acting')
                if method in ['get_state','pause','start_game']:self.identity=self.session
                if method not in ['status','can_place','get_save_info']:spectator.optional('overlay',spectator.ensure_overlay)
                command_id=params.get('command_id')
                if command_id is not None and (not isinstance(command_id,str) or not 1<=len(command_id)<=128):fail('INVALID_ARGUMENT','command_id must have 1..128 characters')
                mutate=method not in ['status','get_state','can_place','get_save_info']
                log=DATA/'logs'/'commands.jsonl'
                fingerprint=json.dumps([method,{k:v for k,v in params.items() if k!='command_id'}],sort_keys=True,separators=(',',':'))
                if command_id and log.exists():
                    previous=None
                    for line in log.read_text(encoding='utf-8').splitlines():
                        r=json.loads(line)
                        if r.get('command_id')==command_id:previous=r
                    if previous:
                        if previous['session_id']!=self.session or previous['fingerprint']!=fingerprint:fail('COMMAND_ID_CONFLICT','command_id already identifies a different command or game session')
                        if previous['phase']=='complete':return previous['result']
                        if previous['phase']=='failed':
                            err=previous['error'];fail(err['code'],err['message'],err.get('details'))
                        fail('COMMAND_OUTCOME_UNKNOWN','Command was submitted earlier without a completion receipt. Inspect state; it will not be automatically repeated.',previous)
                record=dict(time=time.time(),session_id=self.session,method=method,command_id=command_id or str(uuid.uuid4()),fingerprint=fingerprint)
                def append(value):
                    with log.open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
                if mutate:append(dict(record,phase='submitted'))
                try:result=self.dispatch(method,params)
                except PvZError as ex:
                    if mutate and ex.code not in ['TIMEOUT','WRITE_FAILED','READ_FAILED']:
                        append(dict(record,phase='failed',error=dict(code=ex.code,message=str(ex),details=ex.details)))
                    raise
                spectator.optional('annotation',spectator.annotate,method,result,self.session)
                if method not in ['status','can_place','get_save_info']:selection.finish_if_left(self)
                if mutate:append(dict(record,phase='complete',result=result))
                return result
            finally:self.close()

def response(controller,request):
    try:
        if not isinstance(request,dict):fail('INVALID_REQUEST','JSON request must be an object')
        return {'id':request.get('id'),'result':controller.execute(request.get('method',''),request.get('params',{}))}
    except Exception as ex:
        return {'id':request.get('id') if isinstance(request,dict) else None,'error':{'code':getattr(ex,'code','INTERNAL_ERROR'),'message':str(ex),'details':getattr(ex,'details',None)}}
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stdio',action='store_true');parser.add_argument('method',nargs='?',default='status');parser.add_argument('params',nargs='?',default='{}');args=parser.parse_args()
    ctl=Controller()
    if args.stdio:
        for line in sys.stdin:
            try:req=json.loads(line);out=response(ctl,req)
            except Exception as ex:out={'id':None,'error':{'code':'INVALID_JSON','message':str(ex)}}
            print(json.dumps(out,ensure_ascii=False),flush=True)
    else:
        out=response(ctl,dict(id=1,method=args.method,params=json.loads(args.params)))
        print(json.dumps(out,ensure_ascii=False,indent=2));return 1 if 'error' in out else 0
    return 0
if __name__=='__main__':sys.exit(main())
