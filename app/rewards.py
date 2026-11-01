"""Reward identities and accessible transcriptions for the pinned Chinese build.

Text is a manual transcription of the game's image resources, not live OCR.
Do not copy the game images into the distribution.
"""
PICKUP_TYPES = (1, 2, 3, 4, 5, 6, 7, 9, 15, 18, 20)
REWARD_TYPES = (7, 9, 15, 18, 20)
NAMES = {15: 'ZOMBIE_NOTE', 18: 'AWARD_MONEY_BAG', 20: 'AWARD_BAG_DIAMOND'}
_LETTERS = {
    10: ('ZombieNote1', '你好，\n吾们既将对您的房子发动全面进贡。\n真诚的僵尸们', '僵尸预告下一关将发动全面进攻。'),
    20: ('ZombieNote2', '您好\n吾们将对您来一次夜间访问，准备好冷饮和新鲜布丁了吗？\n真诚的僵尸们', '僵尸预告夜间进攻。'),
    30: ('ZombieNote3', '您好，\n吾们听说您在开一个好玩的泳池集会。吾们想这很有趣，吾们马上就到\n真诚的僵尸们', '僵尸预告参加泳池集会。'),
    40: ('ZombieNote4', '你好，\n这封信是你妈妈给你的。\n你妈妈叫你到我家来吃“炖肉”\n记得出门时要打开前门，[末尾四字字迹待确认]\n亲爱的妈妈（不是僵尸们）', '僵尸假冒妈妈，要求你出门并打开前门。'),
    50: ('ZombieFinalNote', '愚蠢的房主，\n你没有满足我们正义的要求。建议你，还是应该听我们的话，否则我们将被迫采取非常行动。请立刻交出你的房子和一切。\n此致，\n僵尸博士埃德加', '僵尸博士要求交出房子，并威胁采取行动。'),
}

def letter_for_next_level(level):
    value = _LETTERS.get(level)
    if not value:
        return None
    resource, text, summary = value
    return dict(kind='zombie_letter', next_adventure_level=level,
                completed_adventure_level=level-1, resource_id=('IMAGE_ZOMBIE_FINAL_NOTE' if level==50 else 'IMAGE_ZOMBIE_NOTE'+str(level//10)),
                language='zh-CN', text=text, summary=summary,
                text_source='manual_transcription_of_pinned_game_image',
                preserves_original_typos=True, transcription_complete=level!=40)

def lawn_reward(coin, level, mode):
    if coin['type'] not in REWARD_TYPES:
        return None
    value = dict(coin_id=coin['id'], type=coin['type'], name=NAMES.get(coin['type'], coin['name']),
                 collecting=coin['collecting'], collect_tool='pvz_collect')
    if coin['type'] == 15 and mode == 0:
        value['letter'] = letter_for_next_level(level+1)
    return value

def award_info(scene, mode, next_level):
    # Normal adventure award mapping: the native AwardScreen draws these images
    # according to the next saved level, not the level that just ended.
    if scene != 5 or mode != 0:
        return None
    letter = letter_for_next_level(next_level)
    return dict(kind='letter' if letter else 'level_award', letter=letter)
