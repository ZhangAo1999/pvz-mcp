"""Spectator preferences, measured pacing and HUD messages (no game writes)."""
import ctypes as C
from ctypes import wintypes as W
import json, math, os, subprocess, sys, time, uuid
from pathlib import Path

from paths import APP, DATA
ROOT=DATA
RUNTIME=ROOT/'runtime'
DEFAULTS=dict(speed=1,show_overlay=True,decision_hold_ms=0,commentary_ms=2000,advance_ms=2000)
MUTEX='Local\\PvZToolsetSpectatorHUD-v3-'+__import__('hashlib').sha256(str(DATA).encode()).hexdigest()[:12]

def viewport_layout(width,height,logical_width=800,logical_height=600,hud_width=216,hud_height=30):
    """Physical client pixels; keep the HUD inside the centered game image.

    The original renderer fits an 800x600 canvas without changing its aspect
    ratio. Scale margins, text and panel together instead of using desktop or
    outer-window coordinates (which include borders and fullscreen bars).
    """
    if min(width,height,logical_width,logical_height)<=0:raise ValueError('Invalid viewport size')
    scale=min(width/logical_width,height/logical_height)
    vw=min(width,round(logical_width*scale));vh=min(height,round(logical_height*scale))
    vx=(width-vw)//2;vy=(height-vh)//2
    panel_scale=min(vw/800,vh/600)
    hw=round(hud_width*panel_scale);hh=round(hud_height*panel_scale)
    return dict(scale=panel_scale,viewport=dict(x=vx,y=vy,width=vw,height=vh),
        hud=dict(x=vx+round(12*panel_scale),y=vy+vh-hh-round(8*panel_scale),width=hw,height=hh))

def caption_lines(text,measure,width,max_lines=2):
    """Wrap Chinese/Latin captions by font width, bounded to two rows."""
    lines=['']
    for char in text:
        if measure(lines[-1]+char)>width and lines[-1]:
            if len(lines)==max_lines:
                while lines[-1] and measure(lines[-1]+'…')>width:lines[-1]=lines[-1][:-1]
                lines[-1]+='…';break
            lines.append('')
        lines[-1]+=char
    return lines

def commentary_key(status):
    return [status.get('session_id'),status.get('bridge',{}).get('epoch')]

def set_commentary(params,status):
    if not any(key in params for key in ('plan','action')):raise ValueError('Provide plan or action')
    values={}
    for name,limit in [('plan',96),('action',160)]:
        if name in params:
            text=params[name]
            if not isinstance(text,str) or len(text)>limit:raise ValueError('%s must be text with at most %d characters'%(name,limit))
            if any(ord(c)<32 and not c.isspace() for c in text):raise ValueError('Control characters are not supported')
            values[name]=' '.join(text.split())
    key=commentary_key(status);tick=status.get('bridge',{}).get('total_updates')
    note=read_json(RUNTIME/'commentary.json')
    if note.get('key')!=key or note.get('decision_tick')!=tick:note={}
    note.update(values,key=key,decision_tick=tick,time=time.time())
    note['display_updated']=True
    try:write_json(RUNTIME/'commentary.json',note,retry_timeout=.15)
    except OSError as ex:
        note.update(display_updated=False,warning=str(ex))
        warn('commentary',ex)
    return note

def warn(label,error):
    try:
        directory=ROOT/'logs';directory.mkdir(parents=True,exist_ok=True)
        with (directory/'presentation-warnings.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(time=time.time(),component=label,error=str(error)),ensure_ascii=False)+'\n')
    except OSError:pass

def optional(label,function,*args,**kwargs):
    """Display failures must never turn a successful game action into a failure."""
    try:return function(*args,**kwargs)
    except OSError as ex:warn(label,ex)

def read_text_shared(file):
    """Allow Windows atomic replacement while this reader holds the old file."""
    if os.name!='nt':return Path(file).read_text(encoding='utf-8')
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,W.LPVOID,W.DWORD,W.DWORD,W.HANDLE];k.CreateFileW.restype=W.HANDLE
    k.GetFileSizeEx.argtypes=[W.HANDLE,C.POINTER(C.c_longlong)];k.GetFileSizeEx.restype=W.BOOL
    k.ReadFile.argtypes=[W.HANDLE,W.LPVOID,W.DWORD,C.POINTER(W.DWORD),W.LPVOID];k.ReadFile.restype=W.BOOL
    k.CloseHandle.argtypes=[W.HANDLE]
    handle=k.CreateFileW(str(Path(file).absolute()),0x80000000,7,None,3,0x80,None)
    if handle==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
    try:
        length=C.c_longlong()
        if not k.GetFileSizeEx(handle,C.byref(length)):raise C.WinError(C.get_last_error())
        if not 0<=length.value<=8*1024*1024:raise ValueError('Presentation document too large')
        data=C.create_string_buffer(length.value);count=W.DWORD()
        if not k.ReadFile(handle,data,length.value,C.byref(count),None):raise C.WinError(C.get_last_error())
        return data.raw[:count.value].decode('utf-8-sig')
    finally:k.CloseHandle(handle)

def read_json(file,default=None):
    try:return json.loads(read_text_shared(file))
    except (OSError,ValueError):return {} if default is None else default

def write_json(file,value,retry_timeout=1):
    file=Path(file);file.parent.mkdir(parents=True,exist_ok=True)
    staged=file.with_name(file.name+'.'+str(os.getpid())+'.'+uuid.uuid4().hex+'.staged')
    staged.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    # Windows readers briefly open the target without FILE_SHARE_DELETE. Keep
    # the staged document intact and retry the rename, never a game command.
    deadline=time.monotonic()+retry_timeout
    while True:
        try:os.replace(str(staged),str(file));break
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.01)

def settings():
    data=read_json(ROOT/'presentation.json');result=dict(DEFAULTS)
    for key,lo,hi in [('speed',1,32),('decision_hold_ms',0,5000),('commentary_ms',0,30000),('advance_ms',10,200000)]:
        if type(data.get(key)) is int and lo<=data[key]<=hi and (key!='advance_ms' or data[key]%10==0):result[key]=data[key]
    if type(data.get('show_overlay')) is bool:result['show_overlay']=data['show_overlay']
    return result

def validate(params):
    result=settings()
    for key,lo,hi in [('speed',1,32),('decision_hold_ms',0,5000),('commentary_ms',0,30000),('advance_ms',10,200000)]:
        if key in params:
            if type(params[key]) is not int or not lo<=params[key]<=hi:
                raise ValueError(key+' must be an integer in [%d, %d]'%(lo,hi))
            if key=='advance_ms' and params[key]%10:raise ValueError('advance_ms must be a multiple of 10')
            result[key]=params[key]
    if 'show_overlay' in params:
        if type(params['show_overlay']) is not bool:raise ValueError('show_overlay must be boolean')
        result['show_overlay']=params['show_overlay']
    return result

def advance_request(params):
    if 'ticks' in params and 'duration_ms' in params:raise ValueError('Use ticks or duration_ms, not both')
    if 'ticks' in params:
        ticks=params['ticks']
        if type(ticks) is not int or not 1<=ticks<=20000:raise ValueError('ticks must be an integer in [1, 20000]')
        return ticks,ticks*10
    duration=params.get('duration_ms',settings()['advance_ms'])
    if type(duration) is not int or not 10<=duration<=200000 or duration%10:
        raise ValueError('duration_ms must be a multiple of 10 in [10, 200000]')
    return duration//10,duration

def identity(ctl):
    value=dict(pid=ctl.pid,session_id=ctl.session,game=ctl.manifest['game'],data_va=ctl.base)
    if read_json(RUNTIME/'game.json')!=value:write_json(RUNTIME/'game.json',value)

def ensure_overlay():
    if not settings()['show_overlay']:return
    RUNTIME.mkdir(parents=True,exist_ok=True)
    k=C.WinDLL('kernel32',use_last_error=True)
    k.OpenMutexW.argtypes=[W.DWORD,W.BOOL,W.LPCWSTR];k.OpenMutexW.restype=W.HANDLE
    k.CloseHandle.argtypes=[W.HANDLE]
    handle=k.OpenMutexW(0x100000,False,MUTEX)
    if handle:k.CloseHandle(handle);return
    temp=DATA/'temp';temp.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,TEMP=str(temp),TMP=str(temp),PYTHONUTF8='1',PYTHONIOENCODING='utf-8',
             PYTHONPYCACHEPREFIX=str(DATA/'cache'/'python'))
    with (RUNTIME/'hud.log').open('ab') as log:
        subprocess.Popen([sys.executable,str(APP/'spectator.py')],cwd=str(APP),env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW)

def annotate(method,result,session_id=None):
    state=result.get('state',result) if isinstance(result,dict) else {}
    old=read_json(RUNTIME/'action.json')
    now=time.time();text=None
    if method=='place_plant' and result.get('placed'):
        p=result['created'][0]
        names={0:'豌豆射手',1:'向日葵',2:'樱桃炸弹',3:'坚果'}
        text='种植%s · 第%d行第%d列'%(names.get(p['type'],p.get('name','植物')),p['row'],p['col'])
    elif method=='collect' and result.get('collected'):text='领取铲子奖励' if result.get('pickup_type')==9 else '拾取物品'
    elif method=='start_game':text='开始关卡'
    elif method=='select_seeds':text='选择卡组，开始游戏'
    elif method=='pause':text='暂停推进'
    if text:
        old.update(text=text,time=now,session_id=session_id)
        old['hold_until']=now+settings()['decision_hold_ms']/1000
        write_json(RUNTIME/'action.json',old)

def hold_for_spectator(session_id):
    # Legacy opt-in pause only. Commentary visibility has no simulation delay.
    note=read_json(RUNTIME/'action.json')
    if note.get('session_id')!=session_id:return
    # Re-evaluate the configured hold duration when the user changes settings.
    until=note.get('time',0)+settings()['decision_hold_ms']/1000
    delay=max(0,min(5,until-time.time()))
    if delay:time.sleep(delay)

def base_timing(session_id=None):
    data=read_json(RUNTIME/'timing.json')
    if (data.get('session_id')==session_id and time.time()-data.get('time',0)<5 and
            isinstance(data.get('base_interval_ms'),(int,float)) and 1<=data['base_interval_ms']<=1000):
        return data['base_interval_ms'],'measured'
    return 10.0,'initial_estimate'

def calibrate(ctl):
    interval,source=base_timing(ctl.session)
    if source=='measured':return interval,source
    # The hook heartbeat continues while gameplay is paused. Measure its actual
    # cadence instead of assuming this machine always renders 100 updates/s.
    start=ctl.mailbox();began=time.monotonic();time.sleep(.3);end=ctl.mailbox()
    delta=(end['heartbeat']-start['heartbeat'])&0xffffffff
    elapsed=time.monotonic()-began
    if (start['board']==end['board'] and start['epoch']==end['epoch'] and
            start['total_updates']==end['total_updates'] and delta>=3):
        sample=elapsed*1000/delta
        if 1<=sample<=1000:
            optional('timing',write_json,RUNTIME/'timing.json',dict(session_id=ctl.session,time=time.time(),base_interval_ms=sample))
            return sample,'measured'
    return interval,source

def progress(status):
    cfg=settings();scene=status.get('scene',{});mb=status.get('bridge',{})
    attached=scene.get('board_attached',False)
    remaining=mb.get('remaining',0) if attached else 0
    interval,source=base_timing(status.get('session_id'))
    speed=mb.get('speed',cfg['speed'])
    native_pause=scene.get('game_pause_flag',False)
    if not attached or scene.get('code')!=3:phase=scene.get('name','waiting')
    elif native_pause:phase='native_pause'
    elif remaining:phase='advancing'
    elif mb.get('paused',1):phase='decision'
    else:phase='running'
    estimate=math.ceil(remaining*interval/max(1,speed)) if phase=='advancing' else None
    return dict(phase=phase,speed=speed,preferred_speed=cfg['speed'],remaining_updates=remaining,
        next_decision_in_ms=estimate,estimate_source=source,show_overlay=cfg['show_overlay'],
        decision_hold_ms=cfg['decision_hold_ms'],commentary_ms=cfg['commentary_ms'],advance_ms=cfg['advance_ms'],board_epoch=mb.get('epoch'),session_id=status.get('session_id'))

class HudClock:
    """Pure view model: an uninterrupted decision has one elapsed-time clock."""
    def __init__(self):
        self.key=None;self.phase=None;self.since=0;self.samples=[];self.last_sample=None
        self.cadence=10.0;self.cadence_measured=False;self.last_motion=0
        self.caption_key=None;self.caption_note={};self.caption_since=0
    def update(self,status,now=None):
        now=time.monotonic() if now is None else now
        p=progress(status);mb=status.get('bridge',{});scene=status.get('scene',{})
        key=(status.get('session_id'),p['board_epoch'])
        if key!=self.key or p['phase']!=self.phase:
            self.since=now;self.samples=[];self.last_motion=now;self.last_sample=None
        if self.last_sample and key==self.key:
            previous,t0=self.last_sample;dt=now-t0
            dh=(mb.get('heartbeat',0)-previous.get('heartbeat',0))&0xffffffff
            if dt>=.2 and 0<dh<10000:
                sample=dt*1000/dh
                if 1<=sample<=1000:self.cadence=sample;self.cadence_measured=True
                self.last_sample=(dict(mb),now)
            if mb.get('total_updates')!=previous.get('total_updates'):self.last_motion=now
        else:self.last_sample=(dict(mb),now)
        self.key=key;self.phase=p['phase']
        if p['phase']=='advancing':
            self.samples.append((now,mb.get('total_updates',0),p['speed']))
            self.samples=[s for s in self.samples if now-s[0]<=1.2 and s[2]==p['speed']]
            if len(self.samples)>1:
                t0,n0,_=self.samples[0];dn=(mb.get('total_updates',0)-n0)&0xffffffff
                if now-t0>=.25 and 0<dn<20000:
                    p['next_decision_in_ms']=math.ceil(p['remaining_updates']*(now-t0)*1000/dn)
                    p['estimate_source']='measured_live'
            p['stalled']=now-self.last_motion>1.5
        p['elapsed_seconds']=max(0,now-self.since)
        elapsed=int(p['elapsed_seconds']);clock='%02d:%02d'%(elapsed//60,elapsed%60)
        phase=p['phase']
        if phase=='decision':title='AI 决策中 · 已用时 '+clock;color='#70dca6'
        elif phase=='advancing':title='正在推进 · %d 倍速'%p['speed'];color='#ffd174'
        elif phase=='running':title='持续运行 · %d 倍速'%p['speed'];color='#ffd174'
        elif phase=='native_pause':title='游戏菜单暂停 · 已用时 '+clock;color='#c5b6ff'
        else:
            title={'award':'本关已完成 · 领奖中','level_preparation':'关卡准备中',
                'main_menu':'游戏主菜单','zombies_won':'本关结束','title_or_loading':'游戏加载中'}.get(phase,'等待游戏')
            color='#a8c6e8'
        note=read_json(RUNTIME/'action.json')
        action=note.get('text','读取布局，等待下一步行动') if note.get('session_id')==status.get('session_id') else '读取布局，等待下一步行动'
        if phase=='advancing':
            secondary='等待游戏恢复更新' if p.get('stalled') else '约 %.1f 秒后再次决策'%(p['next_decision_in_ms']/1000)
            secondary+=' · 剩余 %d 次更新'%p['remaining_updates']
        elif phase=='running':secondary='连续推进 · 由工具暂停后进行决策'
        elif phase=='decision':secondary=action
        else:secondary='当前倍速 %d× · 倍速可在观战设置中调整'%p['speed']
        commentary=read_json(RUNTIME/'commentary.json')
        if commentary.get('key')!=commentary_key(status):commentary={}
        if self.caption_key!=key:
            self.caption_key=key;self.caption_note={};self.caption_since=now
        allowed=phase in ('decision','advancing','running','native_pause','award')
        if not allowed:self.caption_note={}
        elif commentary:
            text=(commentary.get('plan',''),commentary.get('action',''))
            old=(self.caption_note.get('plan',''),self.caption_note.get('action',''))
            completing=bool(text[1] and not old[1] and text[0]==old[0])
            if not any(text):self.caption_note={}
            elif (not self.caption_note or completing or
                    (text!=old and now-self.caption_since>=p['commentary_ms']/1000)):
                self.caption_note=dict(commentary);self.caption_since=now
        commentary=self.caption_note
        published=bool(commentary.get('plan') or commentary.get('action'))
        this_decision=published and commentary.get('decision_tick')==mb.get('total_updates')
        p['presentation_state']=phase
        plan=None;actual=None
        if phase=='decision':
            p['presentation_state']='thinking'
            secondary='正在分析局势，确定后发布本轮操作。'
        show=published and allowed
        if show:
            plan=commentary.get('plan') or '按已确定的方案执行。'
            actual=commentary.get('action')
            if not actual:
                actual=(action if note.get('session_id')==status.get('session_id') and
                    note.get('time',0)>=commentary.get('time',0) else '等待执行本轮操作。')
        p['commentary_label']='最近操作说明'
        if phase=='main_menu':secondary='等待开始 · 观战设置 %d×'%p['preferred_speed']
        compact=title
        if phase=='decision':compact='AI 决策中 · '+clock
        elif phase=='advancing':compact='推进中 · %d× · '%p['speed']+('等待更新' if p.get('stalled') else '余 %.1f秒'%(p['next_decision_in_ms']/1000))
        elif phase=='running':compact='运行中 · %d×'%p['speed']
        elif phase=='native_pause':compact='游戏已暂停 · '+clock
        else:compact=title+' · %d×'%p['preferred_speed']
        p['compact_title']=compact
        p.update(plan=plan,action=actual)
        p.update(title=title,secondary=secondary,color=color,base_interval_ms=self.cadence,cadence_measured=self.cadence_measured)
        return p
