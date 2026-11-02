"""Public, contextual MCP help. No process access or game mutations."""

def help_info():
    return dict(coordinates='row, col and card_slot are 1-based; pixels use the native 800x600 canvas.',
        workflow=['Open your own supported game normally; no installer game path is required.',
                  'pvz_status / pvz_list_games -> pvz_attach (choose pid if multiple supported games)',
                  'pvz_get_state also attaches automatically and pauses combat.',
                  'pvz_launch_game(game_path=...) only when no game is running and no launch path has been remembered.',
                  'pvz_start_game(mode="adventure") uses saved progress; omit level for normal play.',
                  'pvz_get_state -> follow guidance.next_actions -> act -> read fresh state.',
                  'In combat: decide, optionally pvz_set_commentary, pvz_advance(duration_ms=..., speed=1), repeat.'],
        tools=dict(pvz_list_modes='Lists mode keys, IDs and tested support; listed does not mean unlocked.',
            pvz_list_games='Read-only discovery; reports supported/unsupported builds, pid and session_id. Explicit pid can inspect a renamed EXE.',
            pvz_attach='Connect to a running supported game without restarting or resetting its board. Multiple games require pid; use expected_session_id to avoid PID reuse.',
            pvz_start_game='Adventure level is an absolute index 1..50: 1-5=5, 2-1=11. Explicit level selects a replay; omit it to continue your save.',
            pvz_select_seeds='Read seed_selection.available_seeds and required_count. Pass plant type IDs, not card slots, in desired deck order.',
            pvz_continue_dialogue='Advance ONE Crazy Dave line using current dialogue.message_index and board_epoch. Never blindly retry with a new command_id.',
            pvz_shovel='Use native shovel on a current plant_id at row/col. Includes shovel tutorial progress and sound.',
            pvz_place_plant='Use a current card_slot with board_epoch. Conveyor returns only cards with at least 20% of their width exposed. Use the returned native slot number, never array index; hidden slots are rejected. Pass expected_cards_revision and reread after every use.',
            pvz_advance='duration_ms is GAME time, in multiples of 10. Default speed 1. wait=false returns ETA; poll status, not get_state, while waiting.',
            pvz_get_state='Pauses combat. Intro/dialogue/shovel UI continues at native 1x while waiting for inputs. Snapshots are checked between App updates. During noninteractive intro poll status.',
            pvz_observe='Read-only live board observation; never pauses or consumes the advance budget. Requires attachment. Retry UNSTABLE_SNAPSHOT. For decision actions use a paused get_state snapshot.',
            pvz_collect='Use a current coin_id; no spawning. Supports sun/money, final seed packets, shovel, zombie letters (type 15), money bags (18), diamond bags (20). Collect then advance to settle.',
            pvz_read_letter='Read an on-lawn or displayed zombie letter as accessible Chinese text. Uses verified image transcription, not OCR. Does not collect or dismiss the letter.',
            pvz_admin_set_progress='Administrator save edit, separate from normal play; backups and current save/session guards required.'),
        rules=['No automatic strategy loop is built into these tools; the client AI makes decisions.',
               'admin_set_sun and admin_set_progress are administrator actions; normal gameplay does not require them.',
               'On stale identity/card/plant errors read fresh state. On timeout inspect state; do not blindly repeat mutations.'])

def hints(value):
    scene=value.get('scene')
    epoch=value.get('board_epoch') or (scene.get('board_epoch') if isinstance(scene,dict) else None)
    if isinstance(scene,dict):scene=scene.get('code')
    phase=value.get('interaction',{}).get('phase')
    chooser=value.get('seed_selection')
    reward=next((x for x in value.get('coins',[]) if x.get('type') in (7,9,15,18,20)),None) if scene==3 else None
    if reward:
        phase='settling' if reward.get('collecting') else 'reward_pickup'
        actions=['pvz_advance'] if reward.get('collecting') else ['pvz_collect']
        note='Reward is collecting. Advance to finish native award animation.' if reward.get('collecting') else 'The level reward is on the lawn. Collect its current coin_id, then advance the native award animation.'
    elif phase=='dialogue':
        actions=['pvz_continue_dialogue'];note='Read dialogue.text, then advance one line with expected_message_index and expected_board_epoch.'
    elif phase=='shovel_tutorial':
        actions=['pvz_shovel'];note='Dig the three preplaced Peashooters one at a time using plant_id, row and col from plants. Read fresh state after each removal.'
    elif chooser:
        actions=['pvz_select_seeds'];note='Choose exactly required_count unlocked plant type IDs from seed_selection.available_seeds.'
    elif scene==2:
        actions=['pvz_status'];note='Noninteractive intro animation is running. Wait briefly and query status until an input stage or combat begins.'
    elif scene==3:
        actions=['pvz_can_place','pvz_place_plant','pvz_shovel','pvz_advance'];note='Use current board_epoch. Conveyor cards are exposed only after 20% becomes visible. Use returned slot numbers (not array indices), pass expected_cards_revision and reread after each use. If no cards are visible, advance briefly. Sun and normal cooldown do not apply.' if value.get('seed_bank',{}).get('kind')=='conveyor' else 'Use current card slots and board_epoch. can_place checks native legality, sun and cooldown.'
    elif scene==1:
        actions=['pvz_start_game','pvz_list_modes'];note='Start adventure without level to use saved progress. Explicit level selects a replay.'
    elif scene==5:
        actions=['pvz_read_letter','pvz_return_to_menu','pvz_start_game'] if (value.get('award') or {}).get('letter') else ['pvz_return_to_menu','pvz_start_game']
        note='Letter screen: use pvz_read_letter for accessible text. Leave the screen open for viewing; return to menu only when finished.' if (value.get('award') or {}).get('letter') else 'Level award screen. Return to menu and start adventure without level to follow saved progress.'
    else:
        actions=['pvz_status'];note='Inspect readiness; if not running use pvz_launch_game. pvz_help explains all workflows.'
    ctx=value.get('interaction',{})
    if scene==3 and not ctx.get('shovel',{}).get('available',True):actions=[x for x in actions if x!='pvz_shovel']
    example=None
    if phase=='reward_pickup':example=dict(tool='pvz_collect',arguments=dict(coin_id=reward['id'],expected_board_epoch=epoch))
    elif phase=='dialogue':example=dict(tool='pvz_continue_dialogue',arguments=dict(expected_board_epoch=epoch,expected_message_index=ctx['dialogue']['message_index']))
    elif phase=='shovel_tutorial' and value.get('plants'):
        p=value['plants'][0];example=dict(tool='pvz_shovel',arguments=dict(plant_id=p['id'],row=p['row'],col=p['col'],expected_board_epoch=epoch))
    elif scene==1:example=dict(tool='pvz_start_game',arguments=dict(mode='adventure'))
    elif chooser:
        types=[x['type'] for x in chooser.get('available_seeds',[]) if x.get('unlocked',True)]
        count=chooser.get('required_count',0)
        if count and len(types)>=count:example=dict(tool='pvz_select_seeds',arguments=dict(expected_board_epoch=epoch,seeds=types[:count]))
    elif scene==3:example=dict(tool='pvz_advance',arguments=dict(duration_ms=2000,speed=1,expected_board_epoch=epoch))
    return dict(phase=phase or ('seed_selection' if chooser else {0:'loading',1:'menu',2:'preparation',3:'combat',4:'defeat',5:'award'}.get(scene,'other')),next_actions=actions,instruction=note,help_tool='pvz_help',example=example)
