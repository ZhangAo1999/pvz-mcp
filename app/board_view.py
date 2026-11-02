"""Text-only lawn view model; uses native grid coordinates, never screen guesses."""
import tkinter as tk
import hashlib
from tkinter import font as tkfont

# Native seed type order. Colors retain contrast against the pale lawn cells.
NAMES = ['豌豆射手','向日葵','樱桃炸弹','坚果墙','土豆雷','寒冰射手','大嘴花','双发射手',
         '小喷菇','阳光菇','大喷菇','墓碑吞噬者','魅惑菇','胆小菇','寒冰菇','毁灭菇',
         '睡莲','窝瓜','三线射手','缠绕海草','火爆辣椒','地刺','火炬树桩','高坚果',
         '海蘑菇','路灯花','仙人掌','三叶草','裂荚射手','杨桃','南瓜头','磁力菇',
         '卷心菜投手','花盆','玉米投手','咖啡豆','大蒜','叶子保护伞','金盏花','西瓜投手',
         '机枪射手','双子向日葵','香蒲','忧郁菇','吸金磁','地刺王','冰西瓜','玉米加农炮','模仿者']
GLYPHS = ['豌豆','向日葵','樱桃','坚果','土豆雷','寒冰','大嘴花','双发',
          '小喷菇','阳光菇','大喷菇','吞碑','魅惑菇','胆小菇','寒冰菇','毁灭菇',
          '睡莲','窝瓜','三线','海草','辣椒','地刺','火炬','高坚果',
          '海蘑菇','路灯花','仙人掌','三叶草','裂荚','杨桃','南瓜','磁力菇',
          '卷心菜','花盆','玉米','咖啡豆','大蒜','保护伞','金盏花','西瓜',
          '机枪','双子葵','香蒲','忧郁菇','吸金磁','地刺王','冰西瓜','玉米炮','模仿者']


def plant_style(type_id):
    if type(type_id) is not int or not 0 <= type_id < len(GLYPHS):
        return ('?', '#525b62', '未知植物 %s' % type_id)
    color = '#27723e'
    if type_id in (1,9,25,38,41): color = '#b46b00'
    elif type_id in (5,14,46): color = '#087da7'
    elif type_id in (2,15,20): color = '#bd3838'
    elif type_id in (3,4,17,23,30,33): color = '#896037'
    elif type_id in (8,10,11,12,13,24,31,43,44): color = '#80449c'
    return (GLYPHS[type_id], color, NAMES[type_id])


def lawn_model(snapshot):
    rows = (snapshot.get('terrain') or {}).get('playable_rows')
    rows = rows if rows in (5,6) else 6
    cells = {(r,c): [] for r in range(1,rows+1) for c in range(1,10)}
    for plant in snapshot.get('plants', []):
        cell = (plant.get('row'), plant.get('col'))
        if cell in cells:
            cells[cell].append(plant)
    zombies = {r: 0 for r in range(1,rows+1)}
    for zombie in snapshot.get('zombies', []):
        if zombie.get('row') in zombies:
            zombies[zombie['row']] += 1
    return dict(rows=rows, cells=cells, zombies=zombies)


def zombie_row_position(zombie, rows):
    """Map native sprite coordinates to the lawn's continuous row coordinate."""
    y=zombie.get('y')
    if isinstance(y,(int,float)):
        spacing=85 if rows==6 else 100
        return (y-50)/spacing+.5
    return zombie['row']-.5


def separate_zombies(zombies, previous):
    """Keep an entity's visual offset; allocate separated lanes at spawn time."""
    keyed=[(str(z.get('id',z.get('handle',i))),z) for i,z in enumerate(zombies)]
    result={key:previous[key] for key,z in keyed if key in previous}
    placed=[(z,result[key]) for key,z in keyed if key in result]
    for key,z in sorted(keyed,key=lambda pair:pair[0]):
        if key in result:continue
        seed=int(hashlib.sha256(key.encode()).hexdigest()[:8],16)
        jitter=((seed%101)/100-.5)*.012
        side=1 if seed&1 else -1
        candidates=[side*.10+jitter,-side*.10+jitter,jitter]
        neighbors=[offset for other,offset in placed if other.get('row')==z.get('row')
                   and isinstance(other.get('x'),(int,float)) and isinstance(z.get('x'),(int,float))
                   and abs(other['x']-z['x'])<95]
        offset=min(candidates,key=lambda candidate:sum(max(0,.16-abs(candidate-old)) for old in neighbors))
        result[key]=offset;placed.append((z,offset))
    return result


class LawnCanvas(tk.Canvas):
    def __init__(self, parent, detail):
        super().__init__(parent, background='#ffffff', highlightthickness=0, height=280)
        self.snapshot = None
        self.detail = detail
        self.font_sizes = {}
        self.zombie_offsets = {}
        self.board_key = None
        self.bind('<Configure>', lambda event: self.redraw())

    def fit_size(self, text, width, height, maximum=20):
        """Use measured font bounds so multi-character names stay inside cells."""
        key=(text,round(width),round(height),maximum)
        if key not in self.font_sizes:
            size=9
            for candidate in range(maximum,8,-1):
                font=tkfont.Font(self,family='Microsoft YaHei UI',size=candidate,weight='bold')
                if font.measure(text)<=width and font.metrics('linespace')<=height:
                    size=candidate;break
            self.font_sizes[key]=size
        return self.font_sizes[key]

    def show(self, snapshot):
        key=(snapshot.get('session_id'),snapshot.get('board_epoch')) if snapshot else None
        if key!=self.board_key:
            self.zombie_offsets={};self.board_key=key
        self.snapshot = snapshot
        self.redraw()

    def redraw(self):
        self.delete('all')
        width, height = self.winfo_width(), self.winfo_height()
        if not self.snapshot:
            self.create_text(width/2, height/2, text='连接游戏并进入关卡后显示棋盘\n游戏进行时自动更新',
                             justify='center', fill='#69766a', font=('Microsoft YaHei UI',12))
            return
        model = lawn_model(self.snapshot)
        plant_labels=[]
        left, top, right, bottom = 36, 32, 88, 12
        cw = max(1,(width-left-right)/9)
        ch = max(1,(height-top-bottom)/model['rows'])
        for col in range(1,10):
            self.create_text(left+(col-.5)*cw, 15, text=str(col), fill='#637163')
        self.create_text(width-right/2,15,text='右侧 / 入场',fill='#8d4242')
        for row in range(1,model['rows']+1):
            y = top+(row-1)*ch
            self.create_text(16,y+ch/2,text=str(row),fill='#637163')
            for col in range(1,10):
                x = left+(col-1)*cw
                tag = 'cell-%d-%d' % (row,col)
                water = self.snapshot.get('background_type') in (2,3) and row in (3,4)
                bg = '#e4f2f7' if water else ('#edf5e8' if (row+col)%2 else '#e2efda')
                self.create_rectangle(x,y,x+cw,y+ch,fill=bg,outline='#ffffff',width=2,tags=tag)
                plants = model['cells'][row,col]
                if not plants:
                    self.create_text(x+cw/2,y+ch/2,text='·',fill='#aebca7',tags=tag)
                for index,plant in enumerate(plants):
                    glyph,color,_ = plant_style(plant.get('type'))
                    size=self.fit_size(glyph,cw-10,ch/len(plants)-6)
                    label=self.create_text(x+cw/2,y+(index+.5)*ch/len(plants),text=glyph,fill=color,
                                           font=('Microsoft YaHei UI',size,'bold'),tags=tag)
                    plant_labels.append((row,self.bbox(label)))
                self.tag_bind(tag,'<Button-1>',lambda event,r=row,c=col,p=plants:self.inspect(r,c,p))
        # Horizontal placement stays exact; stable visual offsets separate labels.
        zombies=self.snapshot.get('zombies', [])
        self.zombie_offsets=separate_zombies(zombies,self.zombie_offsets)
        for index,zombie in enumerate(zombies):
            row,x = zombie.get('row'),zombie.get('x')
            if row not in range(1,model['rows']+1) or not isinstance(x,(int,float)):
                continue
            px = left+(x-40)/80*cw
            offscreen = px>width-60 or px<left+24
            px=max(left+24,min(width-60,px))
            eating = zombie.get('eating',False)
            text = {1:'旗帜',2:'路障',3:'撑杆',4:'铁桶',5:'读报',6:'铁门',7:'橄榄球',
                    16:'气球',20:'飞贼',23:'巨人'}.get(zombie.get('type'),'普僵')
            if offscreen:text+='›' if x>760 else '‹'
            identity=str(zombie.get('id',zombie.get('handle',index)))
            position=zombie_row_position(zombie,model['rows'])+self.zombie_offsets[identity]
            position=max(row-.62,min(row-.38,position))
            y=top+position*ch
            tag='zombie-%d'%index
            # A pale backing keeps enemies distinguishable from plant abbreviations.
            size=self.fit_size(text,width-12,ch-10,maximum=17)
            label=self.create_text(px,y,text=text,font=('Microsoft YaHei UI',size,'bold'),fill='#c42626' if eating else '#5a5367',tags=tag)
            bounds=self.bbox(tag)
            overlaps=[b for r,b in plant_labels if r==row and
                      bounds[0]<b[2]+3 and bounds[2]>b[0]-3 and bounds[1]<b[3]+3 and bounds[3]>b[1]-3]
            if overlaps:
                plant=min(overlaps,key=lambda b:abs((b[0]+b[2])/2-px))
                label_width=bounds[2]-bounds[0];label_height=bounds[3]-bounds[1]
                # Start the enemy name above the plant's right-hand character.
                px=min(width-label_width/2-4,max(px,(plant[0]+plant[2])/2+label_width/2))
                y=max(label_height/2+20,plant[1]-label_height/2-5)
                self.coords(label,px,y)
                bounds=self.bbox(label)
            # Row identity takes priority over plant avoidance. Constrain the
            # entire label, including its backing, after every displacement.
            row_top=top+(row-1)*ch+4
            row_bottom=top+row*ch-4
            dy=max(row_top-bounds[1],min(0,row_bottom-bounds[3]))
            if dy:
                self.move(label,0,dy)
                bounds=self.bbox(label)
            rect=self.create_rectangle(bounds[0]-3,bounds[1]-1,bounds[2]+3,bounds[3]+1,
                fill='#ffe3dc' if eating else '#f5f1f8',outline='#d54b36' if eating else '#d2c7dc',tags=tag)
            self.tag_lower(rect,tag)
            self.tag_bind(tag,'<Button-1>',lambda event,z=zombie:self.inspect_zombie(z))

    def inspect_zombie(self,zombie):
        self.detail.set('僵尸 %s · 第 %s 行 · 位置 (%.1f, %.1f) · HP %s · 头盔 %s · 盾牌 %s%s' % (
            zombie.get('name',zombie.get('type')),zombie.get('row'),zombie.get('x',0),zombie.get('y',0) or 0,
            zombie.get('hp','?'),zombie.get('helmet_hp',0),zombie.get('shield_hp',0),' · 正在啃咬' if zombie.get('eating') else ''))

    def inspect(self, row, col, plants):
        labels = ['%s（HP %s/%s%s）' % (plant_style(p.get('type'))[2],p.get('hp','?'),p.get('max_hp','?'),
                  '，睡眠' if p.get('sleeping') else '') for p in plants]
        self.detail.set('第 %d 行 · 第 %d 列：%s' % (row,col,'；'.join(labels) if labels else '空地'))
