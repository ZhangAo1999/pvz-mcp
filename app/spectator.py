"""Click-through, non-activating spectator HUD and optional settings panel.

The HUD opens the game with read/query access only. It never controls gameplay.
The separate settings panel uses the same bounded controller API as MCP.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib, json, os, queue, struct, sys, threading, time
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
import presentation as ui

u=C.WinDLL('user32',use_last_error=True);k=C.WinDLL('kernel32',use_last_error=True)
u.GetAncestor.argtypes=[W.HWND,W.UINT];u.GetAncestor.restype=W.HWND
u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)];u.GetWindowThreadProcessId.restype=W.DWORD
u.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)];u.GetClientRect.restype=W.BOOL
u.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)];u.ClientToScreen.restype=W.BOOL
u.IsWindowVisible.argtypes=[W.HWND];u.IsWindowVisible.restype=W.BOOL
u.IsIconic.argtypes=[W.HWND];u.IsIconic.restype=W.BOOL
u.IsWindow.argtypes=[W.HWND];u.IsWindow.restype=W.BOOL
u.GetWindowLongPtrW.argtypes=[W.HWND,C.c_int];u.GetWindowLongPtrW.restype=C.c_ssize_t
u.SetWindowLongPtrW.argtypes=[W.HWND,C.c_int,C.c_ssize_t];u.SetWindowLongPtrW.restype=C.c_ssize_t
u.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT];u.SetWindowPos.restype=W.BOOL
u.ShowWindow.argtypes=[W.HWND,C.c_int];u.ShowWindow.restype=W.BOOL
u.GetForegroundWindow.restype=W.HWND
k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
k.CloseHandle.argtypes=[W.HANDLE]
k.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];k.GetExitCodeProcess.restype=W.BOOL
k.GetProcessTimes.argtypes=[W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)];k.GetProcessTimes.restype=W.BOOL
k.ReadProcessMemory.argtypes=[W.HANDLE,W.LPCVOID,W.LPVOID,C.c_size_t,C.POINTER(C.c_size_t)];k.ReadProcessMemory.restype=W.BOOL
k.CreateMutexW.argtypes=[W.LPVOID,W.BOOL,W.LPCWSTR];k.CreateMutexW.restype=W.HANDLE
k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)];k.QueryFullProcessImageNameW.restype=W.BOOL
p=C.WinDLL('psapi',use_last_error=True)
p.EnumProcesses.argtypes=[C.POINTER(W.DWORD),W.DWORD,C.POINTER(W.DWORD)];p.EnumProcesses.restype=W.BOOL
CALLBACK=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
u.EnumWindows.argtypes=[CALLBACK,W.LPARAM];u.EnumWindows.restype=W.BOOL

def dpi_awareness():
    try:
        u.SetProcessDpiAwarenessContext.argtypes=[W.HANDLE]
        u.SetProcessDpiAwarenessContext(W.HANDLE(-4))
    except AttributeError:pass

class ReadOnlyGame:
    def __init__(self):
        self.handle=None;self.identity=None;self.window=None;self.last_search=-10
        self.manifest=ui.read_json(ui.ROOT/'bridge-manifest.json')
        self.path=os.path.normcase(os.path.abspath(self.manifest['game']))
    def close(self):
        if self.handle:k.CloseHandle(self.handle)
        self.handle=None;self.window=None;self.identity=None
    def read(self,a,n):
        if not self.handle or not 0x10000<=a<a+n<=0x80000000:raise OSError('Invalid read')
        buf=C.create_string_buffer(n);count=C.c_size_t()
        if not k.ReadProcessMemory(self.handle,a,buf,n,C.byref(count)) or count.value!=n:raise OSError('Game unavailable')
        return buf.raw
    def number(self,a):return struct.unpack('<I',self.read(a,4))[0]
    def alive(self):
        if self.handle:
            code=W.DWORD()
            if k.GetExitCodeProcess(self.handle,C.byref(code)) and code.value==259:return True
        return False
    def discover(self):
        """Find a new session after a plain EXE launch, without a controller call."""
        ids=(W.DWORD*8192)();needed=W.DWORD();matches=[]
        if not p.EnumProcesses(ids,C.sizeof(ids),C.byref(needed)):raise OSError('Cannot enumerate game')
        for pid in ids[:needed.value//4]:
            handle=k.OpenProcess(0x1000,False,pid)
            if not handle:continue
            try:
                name=C.create_unicode_buffer(32768);size=W.DWORD(len(name))
                if (k.QueryFullProcessImageNameW(handle,0,name,C.byref(size)) and
                        os.path.normcase(os.path.abspath(name.value))==self.path):matches.append(pid)
            finally:k.CloseHandle(handle)
        if len(matches)>1:raise OSError('More than one matching game; HUD will wait')
        return matches[0] if matches else None
    def bind(self):
        if self.alive():return True
        self.close()
        now=time.monotonic()
        if now-self.last_search<1:return False
        self.last_search=now
        pid=self.discover()
        if pid is None:return False
        if hashlib.sha256(Path(self.path).read_bytes()).hexdigest()!=self.manifest['sha256']:
            raise OSError('Game executable does not match the verified build')
        self.handle=k.OpenProcess(0x1010,False,pid) # QUERY_LIMITED + VM_READ only
        if not self.handle:return False
        try:
            name=C.create_unicode_buffer(32768);size=W.DWORD(len(name))
            if (not k.QueryFullProcessImageNameW(self.handle,0,name,C.byref(size)) or
                    os.path.normcase(os.path.abspath(name.value))!=self.path):raise OSError('Game path changed')
            times=[W.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(self.handle,*[C.byref(t) for t in times]):raise OSError('Cannot identify game')
            created=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
            if self.read(self.manifest['data_va'],8)!=b'PZCT'+struct.pack('<I',self.manifest['version']):
                raise OSError('Game bridge does not match')
            self.identity=dict(pid=pid,session_id='%s:%s'%(pid,created),game=self.manifest['game'],data_va=self.manifest['data_va'])
            ui.write_json(ui.RUNTIME/'game.json',self.identity)
            return True
        except Exception:self.close();raise
    def locate_window(self):
        if self.window and u.IsWindow(self.window):
            pid=W.DWORD();u.GetWindowThreadProcessId(self.window,C.byref(pid))
            if pid.value==self.identity['pid']:return self.window
        self.window=None
        found=[]
        @CALLBACK
        def visit(hwnd,param):
            pid=W.DWORD();u.GetWindowThreadProcessId(hwnd,C.byref(pid))
            if pid.value==self.identity['pid'] and u.IsWindowVisible(hwnd):
                rect=W.RECT();u.GetClientRect(hwnd,C.byref(rect))
                if rect.right>=400 and rect.bottom>=300:found.append((rect.right*rect.bottom,hwnd))
            return True
        u.EnumWindows(visit,0)
        if found:self.window=max(found)[1]
        return self.window
    def status(self):
        if not self.bind():return None
        identity=self.identity;manifest=self.manifest
        raw=self.read(identity['data_va'],128)
        mb={name:struct.unpack_from('<I',raw,off)[0] for name,off in manifest['mailbox'].items()}
        if mb.get('app_busy') or mb['busy']:return None
        app=self.number(0x755e0c);scene=self.number(app+0x920);board=self.number(app+0x868)
        attached=bool(board and board==mb['board'])
        paused=bool(self.read(board+0x17c,1)[0]) if attached else False
        after=self.read(identity['data_va'],128)
        if struct.unpack_from('<I',after,124)[0]!=mb['app_heartbeat'] or struct.unpack_from('<I',after,116)[0]:return None
        names={0:'title_or_loading',1:'main_menu',2:'level_preparation',3:'playing',4:'zombies_won',5:'award',6:'credits',7:'challenge_menu'}
        logical=struct.unpack('<II',self.read(app+0xc0,8))
        if not (400<=logical[0]<=4096 and 300<=logical[1]<=2160):logical=(800,600)
        return dict(session_id=identity['session_id'],bridge=mb,logical_size=logical,
            scene=dict(code=scene,name=names.get(scene,'unknown'),board_attached=attached,game_pause_flag=paused))

class Overlay:
    WIDTH=216;HEIGHT=30;TEXT_WIDTH=312;TRANSPARENT='#010203'
    def __init__(self):
        self.game=ReadOnlyGame();self.clock=ui.HudClock();self.visible=False;self.owner=None;self.last_publish=0
        # The unowned root is the watcher. Only its replaceable panel belongs to
        # the game: Windows may destroy an owned popup when that game exits.
        self.root=tk.Tk();self.root.withdraw();self.root.title('PvZ HUD watcher')
        self.body_font=tkfont.Font(root=self.root,family='Microsoft YaHei UI',size=-12)
        self.title_font=tkfont.Font(root=self.root,family='Microsoft YaHei UI',size=-15,weight='bold')
        self.panel=None;self.hwnd=None;self.scale=1;self.last_size=None
        self.comment_panel=None;self.comment_hwnd=None;self.comment_visible=False;self.last_comment_size=None
        self.root.after(50,self.poll)
    def discard_panel(self):
        for panel in (self.panel,self.comment_panel):
            if panel:
                try:panel.destroy()
                except tk.TclError:pass
        self.panel=None;self.hwnd=None;self.visible=False;self.owner=None;self.last_size=None
        self.comment_panel=None;self.comment_hwnd=None;self.comment_visible=False;self.last_comment_size=None
    def popup(self,owner,title,transparent=False):
        panel=tk.Toplevel(self.root);panel.withdraw();panel.overrideredirect(True)
        panel.title(title);panel.attributes('-alpha',1.0 if transparent else .68)
        background=self.TRANSPARENT if transparent else '#142024'
        panel.configure(bg=background)
        if transparent:panel.attributes('-transparentcolor',self.TRANSPARENT)
        canvas=tk.Canvas(panel,bg=background,highlightthickness=0)
        canvas.pack(fill='both',expand=True);panel.update_idletasks()
        hwnd=u.GetAncestor(panel.winfo_id(),2)
        style=u.GetWindowLongPtrW(hwnd,-20)
        u.SetWindowLongPtrW(hwnd,-20,style|0x80000|0x80|0x08000000|0x20)
        u.SetWindowLongPtrW(hwnd,-8,owner)
        return panel,canvas,hwnd
    def ensure_panel(self,owner):
        if self.panel and self.owner==owner and u.IsWindow(self.hwnd) and u.IsWindow(self.comment_hwnd):return
        self.discard_panel()
        self.panel,self.canvas,self.hwnd=self.popup(owner,'PvZ AI Spectator HUD')
        self.comment_panel,self.comment_canvas,self.comment_hwnd=self.popup(owner,'PvZ AI Operation Summary',transparent=True)
        self.owner=owner
    def layout(self,view,width,height,logical):
        s=ui.viewport_layout(width,height,*logical)['scale']
        self.body_font.configure(size=-max(7,round(12*s)))
        self.title_font.configure(size=-max(8,round(12*s)))
        inner=round((self.TEXT_WIDTH-12)*s)
        texts=([('打算：'+view['plan'],'#fff4c9'),('操作：'+view['action'],'#ffffff')]
            if view.get('plan') is not None else [])
        captions=[(ui.caption_lines(text,self.body_font.measure,inner),color) for text,color in texts]
        line_height=self.body_font.metrics('linespace')
        pixel_height=sum(len(lines)*line_height for lines,_ in captions)+round((8+3*(len(captions)-1))*s)
        layout=ui.viewport_layout(width,height,*logical)
        if captions:
            h=layout['hud']
            layout['commentary']=dict(x=h['x'],y=h['y']-round(6*s)-pixel_height,width=round(self.TEXT_WIDTH*s),height=pixel_height)
        return layout,captions,line_height
    def draw(self,view,layout,captions,line_height):
        s=layout['scale'];box=layout['hud'];size=(box['width'],box['height'])
        if size!=self.last_size:
            self.panel.geometry('%dx%d'%size);self.canvas.configure(width=size[0],height=size[1])
            self.panel.update_idletasks();self.last_size=size
        width,height=size
        c=self.canvas;c.delete('all')
        c.create_rectangle(0,0,width-1,height-1,fill='#142024',outline='#45555d')
        c.create_rectangle(0,0,round(3*s),height,fill=view['color'],outline='')
        headline=ui.caption_lines(view.get('compact_title',view['title']),self.title_font.measure,width-round(20*s),max_lines=1)[0]
        c.create_text(round(10*s),round(15*s),text=headline,fill=view['color'],font=self.title_font,anchor='w')
        if not captions:return
        size=(layout['commentary']['width'],layout['commentary']['height'])
        if size!=self.last_comment_size:
            self.comment_panel.geometry('%dx%d'%size);self.comment_canvas.configure(width=size[0],height=size[1])
            self.comment_panel.update_idletasks();self.last_comment_size=size
        c=self.comment_canvas;c.delete('all');width,height=size
        y=round(4*s);radius=max(1,round(s))
        for lines,color in captions:
            for line in lines:
                x=round(6*s)
                for dx,dy in [(-radius,0),(radius,0),(0,-radius),(0,radius),(-radius,-radius),(-radius,radius),(radius,-radius),(radius,radius)]:
                    c.create_text(x+dx,y+dy,text=line,fill='#0a0c0e',font=self.body_font,anchor='nw',tags='outline')
                c.create_text(x,y,text=line,fill=color,font=self.body_font,anchor='nw',tags='caption')
                y+=line_height
            y+=round(3*s)
    def hide(self):
        if self.visible and self.hwnd and u.IsWindow(self.hwnd):u.ShowWindow(self.hwnd,0)
        if self.comment_visible and self.comment_hwnd and u.IsWindow(self.comment_hwnd):u.ShowWindow(self.comment_hwnd,0)
        self.visible=False
        self.comment_visible=False
    def poll(self):
        try:
            cfg=ui.settings()
            if not cfg['show_overlay']:self.hide();self.root.after(150,self.poll);return
            status=self.game.status()
            if status:
                hwnd=self.game.locate_window()
                if hwnd and not u.IsIconic(hwnd) and u.IsWindowVisible(hwnd):
                    rect=W.RECT();point=W.POINT(0,0)
                    u.GetClientRect(hwnd,C.byref(rect));u.ClientToScreen(hwnd,C.byref(point))
                    self.ensure_panel(hwnd)
                    view=self.clock.update(status)
                    layout,captions,line_height=self.layout(view,rect.right,rect.bottom,status['logical_size'])
                    self.draw(view,layout,captions,line_height)
                    # An owned popup stays above this game, below unrelated apps.
                    box=layout['hud'];x=point.x+box['x'];y=point.y+box['y']
                    u.SetWindowPos(self.hwnd,None,x,y,box['width'],box['height'],0x0010|0x0004|0x0040)
                    if not self.visible:u.ShowWindow(self.hwnd,4);self.visible=True
                    comment_box=layout.get('commentary');screen_comment=None
                    if comment_box:
                        screen_comment=dict(comment_box,x=point.x+comment_box['x'],y=point.y+comment_box['y'])
                        u.SetWindowPos(self.comment_hwnd,None,screen_comment['x'],screen_comment['y'],comment_box['width'],comment_box['height'],0x0010|0x0004|0x0040)
                        if not self.comment_visible:u.ShowWindow(self.comment_hwnd,4);self.comment_visible=True
                    elif self.comment_visible:
                        u.ShowWindow(self.comment_hwnd,0);self.comment_visible=False
                    now=time.time()
                    if now-self.last_publish>.5:
                        ui.write_json(ui.RUNTIME/'observer.json',dict(view,time=now,hwnd=int(self.hwnd),
                            owner_hwnd=int(hwnd),watcher_pid=os.getpid(),hud_version=4,game_pid=self.game.identity['pid'],
                            style=dict(status_alpha=.68,commentary_background='transparent',commentary_outline=True),
                            layout=layout,screen_hud=dict(x=x,y=y,width=box['width'],height=box['height']),
                            commentary_hwnd=int(self.comment_hwnd),screen_commentary=screen_comment,
                            click_through=True,no_activate=True,read_only_process_access='0x1010'))
                        if status['scene']['board_attached'] and view['cadence_measured']:
                            ui.write_json(ui.RUNTIME/'timing.json',dict(session_id=status['session_id'],time=now,
                                base_interval_ms=view['base_interval_ms']))
                        self.last_publish=now
                else:self.hide()
            elif not self.game.handle:self.discard_panel()
        except (OSError,ValueError,KeyError,struct.error,tk.TclError) as ex:
            self.discard_panel();self.game.close()
            ui.write_json(ui.RUNTIME/'hud-health.json',dict(time=time.time(),error=str(ex)))
        self.root.after(80 if self.game.handle else 500,self.poll)
    def run(self):
        try:self.root.mainloop()
        finally:self.game.close()

def settings_panel():
    root=tk.Tk();root.title('植物大战僵尸 · 观战设置');root.resizable(False,False)
    frame=ttk.Frame(root,padding=18);frame.pack(fill='both',expand=True)
    cfg=ui.settings();speed=tk.IntVar(value=cfg['speed']);show=tk.BooleanVar(value=cfg['show_overlay'])
    hold=tk.IntVar(value=cfg['commentary_ms']);advance=tk.IntVar(value=cfg['advance_ms'])
    message=tk.StringVar(value='说明与游戏推进同时显示，不增加等待状态。')
    tasks=queue.Queue();results=queue.Queue()
    def worker():
        from pvz import Controller
        ctl=Controller()
        while True:
            params=tasks.get()
            if params is None:return
            try:results.put((True,ctl.execute('set_presentation',params)))
            except Exception as ex:results.put((False,str(ex)))
    threading.Thread(target=worker,daemon=True).start()
    def apply():
        try:
            value=ui.validate(dict(speed=speed.get(),show_overlay=show.get(),commentary_ms=hold.get(),advance_ms=advance.get(),decision_hold_ms=0))
            tasks.put(value);message.set('正在应用…')
        except (ValueError,tk.TclError):message.set('倍速 1–32；说明 0–30000 毫秒；推进 10–200000 毫秒，须为 10 的倍数。')
    def preset(value):speed.set(value);apply()
    ttk.Label(frame,text='观战倍速',font=('Microsoft YaHei UI',12,'bold')).grid(row=0,column=0,sticky='w')
    ttk.Spinbox(frame,from_=1,to=32,width=6,textvariable=speed,command=apply).grid(row=0,column=1,sticky='e')
    presets=ttk.Frame(frame);presets.grid(row=1,column=0,columnspan=2,pady=12)
    for i,value in enumerate([1,2,4,8,16,32]):ttk.Button(presets,text='%d×'%value,width=5,command=lambda v=value:preset(v)).grid(row=0,column=i,padx=2)
    ttk.Checkbutton(frame,text='显示游戏画面提示',variable=show,command=apply).grid(row=2,column=0,columnspan=2,sticky='w')
    ttk.Label(frame,text='说明最短显示（毫秒）').grid(row=3,column=0,pady=(12,4),sticky='w')
    ttk.Spinbox(frame,from_=0,to=30000,increment=500,width=8,textvariable=hold).grid(row=3,column=1,sticky='e')
    ttk.Label(frame,text='每次快进的游戏时长（毫秒）').grid(row=4,column=0,pady=4,sticky='w')
    ttk.Spinbox(frame,from_=10,to=200000,increment=500,width=8,textvariable=advance).grid(row=4,column=1,sticky='e')
    ttk.Button(frame,text='应用设置',command=apply).grid(row=5,column=0,pady=10,sticky='w')
    def defaults():
        speed.set(1);hold.set(2000);advance.set(2000);show.set(True);apply()
    ttk.Button(frame,text='恢复默认',command=defaults).grid(row=5,column=1,pady=10,sticky='e')
    ttk.Label(frame,textvariable=message,wraplength=350).grid(row=6,column=0,columnspan=2,sticky='w')
    ttk.Label(frame,text='说明不会因暂停/推进而闪烁；只更新有变化的内容。默认 1 倍速，每次推进 2000 毫秒约需现实 2 秒。',wraplength=350).grid(row=7,column=0,columnspan=2,pady=(10,0),sticky='w')
    root.bind('<Return>',lambda event:apply())
    def receive():
        try:
            while True:
                ok,value=results.get_nowait()
                message.set('已应用：%d 倍速%s'%(value['settings']['speed'],'，当前游戏已同步' if value.get('game_connected') else '，下次开局生效') if ok else '应用失败：'+value)
        except queue.Empty:pass
        root.after(100,receive)
    root.after(100,receive);root.mainloop();tasks.put(None)

def main():
    dpi_awareness()
    if '--settings' in sys.argv:return settings_panel()
    mutex=k.CreateMutexW(None,False,ui.MUTEX)
    if C.get_last_error()==183:k.CloseHandle(mutex);return
    try:Overlay().run()
    finally:k.CloseHandle(mutex)

if __name__=='__main__':main()
