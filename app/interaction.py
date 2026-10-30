"""Audited 1.2.0.1073 intro state; public raw values retained for diagnostics."""
import struct
import re

def read(mem,app,board):
    index=mem.i32(app+0x974);tutorial=mem.i32(board+0x559c)
    scene=mem.i32(app+0x920)
    phase='dialogue' if scene==2 and index!=-1 else 'shovel_tutorial' if scene==2 and tutorial in (15,16,17) else 'intro' if scene==2 else 'combat' if scene==3 else 'other'
    text=None
    if phase=='dialogue':
        # MSVC basic_string<wchar_t>: 16-byte SSO union, size, capacity.
        raw=mem.read(app+0x97c,24);length,capacity=struct.unpack_from('<II',raw,16)
        if not 0<=length<=4096 or capacity<length:raise ValueError('Invalid Crazy Dave text bounds')
        if length:
            ptr=struct.unpack_from('<I',raw)[0]
            data=mem.read(ptr,length*2) if capacity>=8 else raw[:length*2]
            text=data.decode('utf-16le',errors='replace')
    mode=mem.i32(app+0x91c);level=mem.i32(board+0x5568)
    profile=mem.u32(app+0x950);context_level=mem.i32(profile+0x4c) if profile else level
    bowling=mode in (17,33) or (mode==0 and context_level==5)
    conveyor=mode in (17,33,35,46,25,47,48,26,27,21) or (mode==0 and context_level in (5,10,20,25,30,40,45,50))
    return dict(phase=phase,tutorial_state=tutorial,input_required=phase in ('dialogue','shovel_tutorial'),
        intro_ui_running=scene==2 and not bool(mem.read(board+0x17c,1)[0]),
        bowling=bowling,conveyor=conveyor,
        dialogue=dict(active=phase=='dialogue',message_index=index,
            text=re.sub(r'\{[^}]*\}','',text).strip() if text else text,raw_text=text),
        shovel=dict(available=phase=='shovel_tutorial' or (scene==3 and mode in (0,16) and level>4 and not bowling)))
