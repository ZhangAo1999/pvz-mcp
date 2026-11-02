"""Native desktop controls for the shared controller. Polling never pauses play."""
import json
import os
import queue
import threading
import time
from board_view import LawnCanvas, plant_style
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

import presentation
from paths import DATA, APP

SCENES = {0: '载入中', 1: '主菜单', 2: '开场 / 选卡', 3: '游戏中',
          4: '失败', 5: '过关奖励', 6: '制作人员', 7: '模式选择'}
PHASES = {'decision': 'AI 决策中 / 已暂停', 'advancing': '定时推进中',
          'running': '持续运行中', 'native_pause': '游戏菜单暂停中'}
ACTION_LABELS = {'attach':'连接游戏','launch_game':'启动游戏','start_game':'开始冒险',
                 'pause':'暂停','resume':'持续运行','advance':'推进游戏','return_to_menu':'返回主菜单',
                 'set_presentation':'应用设置','observe':'刷新棋盘'}


def action_params(method, status, values=None):
    """Bind every game action to the observed process, never silently retarget."""
    params = dict(values or {})
    if method in ('attach', 'launch_game', 'set_presentation'):
        return params
    if not status.get('attached') or not status.get('session_id'):
        raise ValueError('请先刷新状态并连接游戏。')
    params['expected_session_id'] = status['session_id']
    epoch = status.get('bridge', {}).get('epoch')
    if epoch is not None:
        params['expected_board_epoch'] = epoch
    return params


def describe(status):
    if not status.get('running'):
        return ('未连接', status.get('reason', '请先启动游戏，然后刷新进程。'), '—')
    if not status.get('attached'):
        return ('发现游戏 · 待接入', '请选择进程并点击「连接」', '—')
    scene = status.get('scene', {})
    p = status.get('presentation', {})
    stage = scene.get('adventure_stage')
    if not stage:
        n = scene.get('profile_adventure_level')
        stage = '%d-%d' % ((n - 1) // 10 + 1, (n - 1) % 10 + 1) if type(n) is int and 1 <= n <= 50 else '—'
    detail = '%s  ·  关卡 %s' % (SCENES.get(scene.get('code'), scene.get('name', '未知')), stage)
    phase = PHASES.get(p.get('phase'), '等待游戏')
    remaining = p.get('next_decision_in_ms')
    if remaining is not None:
        phase += ' · 约 %.1f 秒后暂停' % (remaining / 1000)
    return ('已连接 · PID %s' % status.get('pid'), detail,
            '%s  ·  %s×' % (phase, p.get('speed', 1)))


class Worker:
    """One controller owner. Tk is only accessed by the main thread."""
    def __init__(self, factory=None):
        self.tasks = queue.Queue()
        self.results = queue.Queue()
        self.pending = set()
        self.factory = factory
        threading.Thread(target=self.run, daemon=True).start()

    def submit(self, method, params=None):
        if method in self.pending:
            return False
        self.pending.add(method)
        self.tasks.put((method, dict(params or {})))
        return True

    def run(self):
        if self.factory is None:
            from pvz import Controller
            self.factory = Controller
        ctl = self.factory()
        while True:
            method, params = self.tasks.get()
            try:
                result = ctl.execute(method, params)
                self.results.put((method, result, None))
            except Exception as ex:
                self.results.put((method, None, '%s: %s' % (getattr(ex, 'code', 'ERROR'), ex)))


class Panel:
    def __init__(self, root):
        self.root = root
        self.worker = Worker()
        self.status = {}
        self.games = []
        self.last_json = ''
        self.busy = None
        self.version = json.loads((APP.parent / 'package.json').read_text(encoding='utf-8'))['version']
        root.title('PvZ MCP 控制台 · ' + self.version)
        root.geometry('980x880')
        root.minsize(900, 800)
        from tkinter import font
        font.nametofont('TkDefaultFont').configure(family='Microsoft YaHei UI', size=10)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#f3f6f2')
        style.configure('TLabel', background='#f3f6f2')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'), foreground='#28633d')
        style.configure('TButton', padding=(12, 7))
        body = ttk.Frame(root, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='PvZ MCP 控制台', style='Title.TLabel').pack(anchor='w')
        self.connection = tk.StringVar(value='正在查询游戏…')
        self.scene = tk.StringVar(value='—')
        self.phase = tk.StringVar(value='—')
        header = ttk.Frame(body)
        header.pack(fill='x', pady=(6, 8))
        for var in (self.connection, self.scene, self.phase):
            ttk.Label(header, textvariable=var).pack(side='left', padx=(0, 22))
        self.process = ttk.Combobox(body, state='readonly', width=68)
        self.process.pack(fill='x', pady=(0, 8))
        row = ttk.Frame(body)
        row.pack(fill='x')
        self.button(row, '刷新进程', self.discover)
        self.button(row, '连接所选游戏', self.attach)
        self.button(row, '选择 EXE 并启动', self.launch)
        ttk.Label(row, text='v' + self.version).pack(side='right')
        book = ttk.Notebook(body)
        book.pack(fill='both', expand=True, pady=(12, 8))
        controls = ttk.Frame(book, padding=16)
        state = ttk.Frame(book, padding=12)
        book.add(controls, text='  控制与观战  ')
        book.add(state, text='  文字棋盘  ')
        row = ttk.Frame(controls)
        row.pack(anchor='w', pady=4)
        self.button(row, '开始冒险（当前进度）', lambda: self.action('start_game'))
        self.button(row, '暂停', lambda: self.action('pause'))
        self.button(row, '持续运行', lambda: self.action('resume', {'speed': self.number(self.speed, 1, 32)}))
        self.button(row, '返回主菜单', self.menu)
        row = ttk.Frame(controls)
        row.pack(anchor='w', pady=14)
        ttk.Label(row, text='推进游戏时长（毫秒）').pack(side='left')
        cfg = presentation.settings()
        self.duration = tk.StringVar(value=str(cfg['advance_ms']))
        ttk.Spinbox(row, from_=10, to=200000, increment=1000, textvariable=self.duration, width=9).pack(side='left', padx=8)
        self.button(row, '推进一次 → 自动暂停', self.advance)
        ttk.Separator(controls).pack(fill='x', pady=10)
        settings = ttk.Frame(controls)
        settings.pack(fill='x')
        self.speed = tk.StringVar(value=str(cfg['speed']))
        self.caption = tk.StringVar(value=str(cfg['commentary_ms']))
        self.overlay = tk.BooleanVar(value=cfg['show_overlay'])
        for title, var, low, high, step in [('运行倍速', self.speed, 1, 32, 1), ('操作文字显示（毫秒）', self.caption, 0, 30000, 500)]:
            row = ttk.Frame(settings)
            row.pack(fill='x', pady=9)
            ttk.Label(row, text=title, width=26).pack(side='left')
            ttk.Spinbox(row, from_=low, to=high, increment=step, textvariable=var, width=10).pack(side='left')
        ttk.Checkbutton(settings, text='显示游戏左下角观战提示', variable=self.overlay).pack(anchor='w', pady=10)
        row = ttk.Frame(settings)
        row.pack(anchor='w', pady=12)
        self.button(row, '应用设置', self.apply)
        self.button(row, '填入默认值', self.defaults)
        ttk.Label(settings, text='设置将作为 AI 的默认值，AI 也可在单次操作中调整。\n面板与 AI 共享游戏控制。', wraplength=740).pack(anchor='w', pady=8)
        row = ttk.Frame(state)
        row.pack(fill='x')
        self.button(row, '运行', lambda: self.action('resume', {'speed': self.number(self.speed, 1, 32)}))
        self.button(row, '暂停', lambda: self.action('pause'))
        self.button(row, '复制 JSON', self.copy_json)
        self.button(row, '打开数据目录', lambda: os.startfile(str(DATA)))
        self.button(row, '复制 MCP 配置', self.copy_config)
        self.snapshot_note = tk.StringVar(value='等待游戏')
        ttk.Label(state, textvariable=self.snapshot_note).pack(anchor='w', pady=8)
        self.cell_detail = tk.StringVar(value='点击植物或僵尸查看详情，红色表示正在啃咬。')
        self.lawn = LawnCanvas(state, self.cell_detail)
        self.lawn.pack(fill='both', expand=True)
        legend = ttk.Frame(state)
        legend.pack(fill='x', pady=6)
        for type_id in (0,1,5,3):
            glyph, color, name = plant_style(type_id)
            ttk.Label(legend, text=glyph+' '+name, foreground=color).pack(side='left', padx=(0,14))
        ttk.Label(state, textvariable=self.cell_detail, wraplength=850).pack(anchor='w', pady=4)
        self.snapshot_key = None
        self.notice = tk.StringVar(value='就绪')
        ttk.Label(body, textvariable=self.notice, wraplength=870).pack(anchor='w')
        root.report_callback_exception = lambda cls, ex, tb: messagebox.showerror('操作失败', str(ex), parent=root)
        self.discover()
        root.after(100, self.drain)
        root.after(500, self.poll)

    def button(self, parent, text, command):
        ttk.Button(parent, text=text, command=command).pack(side='left', padx=(0, 8))

    @staticmethod
    def number(var, low, high):
        try:
            n = int(var.get())
        except ValueError:
            raise ValueError('请填写整数。')
        if not low <= n <= high:
            raise ValueError('数值范围为 %d–%d。' % (low, high))
        return n

    def action(self, method, values=None):
        if self.busy:
            self.notice.set('正在执行上一项操作，请稍候。')
            return
        params = action_params(method, self.status, values)
        if self.worker.submit(method, params):
            self.busy = method
            self.notice.set('正在' + ACTION_LABELS.get(method,method) + '…')

    def discover(self):
        self.worker.submit('list_games')
        self.worker.submit('status')

    def attach(self):
        i = self.process.current()
        if i < 0:
            raise ValueError('请先刷新并选择游戏进程。')
        game = self.games[i]
        if not game.get('supported'):
            raise ValueError('此进程版本尚不支持，不能接入。')
        self.action('attach', {'pid': game['pid'], 'expected_session_id': game['session_id']})

    def launch(self):
        path = filedialog.askopenfilename(parent=self.root, title='选择已安装的植物大战僵尸', filetypes=[('游戏程序', '*.exe')])
        if path:
            self.action('launch_game', {'game_path': path})

    def menu(self):
        if self.status.get('scene', {}).get('board_present') and not messagebox.askyesno('返回主菜单', '这将离开当前棋盘。确定继续吗？', parent=self.root):
            return
        self.action('return_to_menu')

    def advance(self):
        n = self.number(self.duration, 10, 200000)
        if n % 10:
            raise ValueError('推进时长必须是 10 毫秒的倍数。')
        self.action('advance', {'duration_ms': n, 'speed': self.number(self.speed, 1, 32), 'wait': False})

    def apply(self):
        values = dict(speed=self.number(self.speed, 1, 32), commentary_ms=self.number(self.caption, 0, 30000),
                      advance_ms=self.number(self.duration, 10, 200000), show_overlay=self.overlay.get())
        presentation.validate(values)
        self.action('set_presentation', values)

    def defaults(self):
        self.speed.set('1'); self.duration.set('2000'); self.caption.set('2000'); self.overlay.set(True)
        self.notice.set('已填入默认值，点击「应用设置」生效。')

    def copy(self, text):
        self.root.clipboard_clear(); self.root.clipboard_append(text)
        self.notice.set('已复制到剪贴板。')

    def copy_json(self):
        self.copy(self.last_json)

    def copy_config(self):
        root = Path(os.environ.get('PVZ_INSTALL_ROOT', str(DATA.parent)))
        config = root / 'mcp-config.json'
        if not config.is_file():
            raise ValueError('尚未找到安装目录的 mcp-config.json，请先运行安装器。')
        self.copy(config.read_text(encoding='utf-8-sig'))

    def display(self, value):
        self.last_json = json.dumps(value, ensure_ascii=False, indent=2)
        snapshot = value if 'plants' in value else value.get('state')
        if not isinstance(snapshot, dict) or 'plants' not in snapshot:
            return
        self.snapshot_key = (snapshot.get('session_id'), snapshot.get('board_epoch'))
        self.lawn.show(snapshot)
        sun = snapshot.get('sun', {})
        sun = sun.get('available', '—') if isinstance(sun, dict) else sun
        self.snapshot_note.set('更新 %s · 阳光 %s · 植物 %d · 僵尸 %d' % (
            time.strftime('%H:%M:%S'), sun, len(snapshot.get('plants', [])), len(snapshot.get('zombies', []))))

    def validate_snapshot(self, status):
        if self.snapshot_key is None:
            return
        key = (status.get('session_id'), status.get('bridge', {}).get('epoch'))
        if not status.get('attached') or not status.get('scene', {}).get('board_present') or key != self.snapshot_key:
            self.snapshot_key = None
            self.lawn.show(None)
            self.snapshot_note.set('等待当前关卡')
            self.cell_detail.set('点击植物或僵尸查看详情，红色表示正在啃咬。')

    def poll(self):
        if not self.busy:
            self.worker.submit('status')
        active = self.status.get('scene', {}).get('board_attached', False)
        self.root.after(250 if active else 1000, self.poll)

    def drain(self):
        try:
            while True:
                method, result, error = self.worker.results.get_nowait()
                self.worker.pending.discard(method)
                if method == self.busy:
                    self.busy = None
                if error:
                    self.notice.set(error)
                    if method == 'observe':
                        self.snapshot_note.set('连接暂不可用 · 等待恢复')
                    if method not in ('status', 'observe', 'list_games'):
                        messagebox.showerror('操作失败', error, parent=self.root)
                    continue
                if method == 'status':
                    self.status = result
                    for var, text in zip((self.connection, self.scene, self.phase), describe(result)):
                        var.set(text)
                    self.validate_snapshot(result)
                    if result.get('attached') and result.get('scene', {}).get('board_attached'):
                        self.worker.submit('observe')
                    if not self.last_json:
                        self.display(result)
                elif method == 'list_games':
                    old = self.process.get()
                    self.games = result.get('games', [])
                    labels = ['PID %s · %s · %s' % (g['pid'], '兼容' if g.get('supported') else '不支持', g.get('path', g.get('game', ''))) for g in self.games]
                    self.process['values'] = labels
                    if old in labels:
                        self.process.current(labels.index(old))
                    elif labels:
                        self.process.current(0)
                    else:
                        self.process.set('未发现游戏进程')
                elif method == 'observe':
                    self.display(result)
                else:
                    self.notice.set(ACTION_LABELS.get(method,method) + '完成')
                    self.display(result)
                    self.worker.submit('status')
        except queue.Empty:
            pass
        self.root.after(50, self.drain)


def main():
    try:
        from spectator import dpi_awareness
        dpi_awareness()
        root = tk.Tk()
        panel = Panel(root)
        # Build-time UI check creates every widget but issues no game action.
        if '--ui-smoke' in __import__('sys').argv:
            root.after(1800, root.destroy)
        root.mainloop()
    except Exception:
        import traceback
        (DATA / 'logs' / 'control-panel-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
