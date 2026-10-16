# MCP 调用说明

调用前使用 `pvz_status` 确认游戏进程与场景。尚未运行时调用 `pvz_launch_game`；如果资源仍在加载，等待后再次查询状态。

## 开始关卡与选卡

`pvz_start_game` 默认按当前存档进度开始冒险。显式传入 `level` 才会临时选择指定冒险关卡；编号 1–50 对应 1-1 至 5-10。

```json
{"mode":"adventure","level":4}
```

临时选关与永久修改存档不同。退出临时重玩时会保留原有进度；重玩期间应通过工具正常返回菜单，避免直接结束进程。

根据返回的阶段继续操作。需要选卡时，读取可用植物和卡组数量，再调用 `pvz_select_seeds`。植物类型 ID 不能重复，卡组数量必须满足游戏要求；模仿者暂不支持。部分早期关卡使用固定卡组，无需选卡。

## 获取状态和操作植物

`pvz_get_state` 会暂停并返回一致的 JSON 快照。AI 应根据快照中的阳光、卡槽、冷却、布局与敌人状态作出判断。

调用 `pvz_can_place` 或 `pvz_place_plant` 的参数示例：

```json
{
  "card_slot":2,
  "row":3,
  "col":1,
  "expected_board_epoch":2,
  "expected_session_id":"从最近状态复制真实值"
}
```

卡槽、行和列均从 1 开始，卡槽参数名为 `card_slot`。`expected_board_epoch` 使用最近快照中的 `board_epoch`，`expected_session_id` 使用当前进程身份；示例值需要替换。切换关卡或重启游戏后，重新获取状态。

`pvz_can_place` 不种植、不扣费，为保证检查一致性可能暂停棋盘。`pvz_place_plant` 按原生规则扣阳光并开始冷却。

变更操作可附带唯一的 `command_id`。重试同一个动作时沿用原 ID，不同动作使用新 ID。请求超时先查询结果，避免重复种植；如果动作结果不明，不要盲目重试。

## 控制时间

`pvz_advance` 接受游戏时长或更新次数：

```json
{"duration_ms":6000,"wait":true}
```

- `1` 次更新对应 `10` 毫秒游戏时间。
- `ticks` 与 `duration_ms` 二选一；均省略时使用观战设置中的 `advance_ms`，默认 2000 毫秒。
- `wait:true` 等待结束并返回暂停快照，阻塞等待上限约 40 秒。
- `wait:false` 立即返回预计等待时间，游戏在有限预算结束后自行暂停。
- 倍速与游戏时长分别设置。原生暂停、结算或棋盘切换可能提前结束推进。

异步推进期间用只读 `pvz_status` 检查状态。提前调用 `pvz_get_state` 会暂停尚未完成的预算。客户端对启动、推进等长操作的超时建议至少设置为 120 秒。

`pvz_resume` 会持续运行，直到主动暂停或原生场景发生变化；它不使用有限推进预算。

## 观战提示

使用 `pvz_set_presentation` 配置速度与提示：

```json
{
  "speed":1,
  "show_overlay":true,
  "advance_ms":2000,
  "commentary_ms":3000,
  "decision_hold_ms":0
}
```

`commentary_ms` 限制说明文字被替换的最快间隔，不会额外阻塞游戏。`decision_hold_ms` 是可选的推进前停留，默认保持 0 即可。速度支持 1–32 倍。

完成本轮操作后，通过 `pvz_set_commentary` 发布简短说明：

```json
{"plan":"等向日葵冷却，6秒后再看","action":"第三路补一株豌豆"}
```

说明中的等待时长应与下一次推进一致。重复等待时不必反复刷新同一句话。每项说明最多显示两行，文字过长会省略。

## 拾取和领奖

`pvz_collect` 使用最新快照 `coins` 数组中的物品 ID，并附带当前棋盘身份。支持已有普通阳光、钱币和冒险 1-4 的铲子，不生成新物品。

领取 1-4 铲子后，一次调用：

```json
{"duration_ms":10000,"wait":false,"speed":1}
```

期间只用 `pvz_status` 等待原生奖励页，不调用 `pvz_get_state` 打断动画。当前适配中，约 6 秒后会进入奖励页；剩余预算不代表奖励页仍在推进。场景切换后不再使用旧棋盘或物品 ID。

## 修改冒险进度

管理员改档操作只在使用者明确要求时调用。先正常回主菜单，再调用 `pvz_get_save_info`，确认 `can_edit_progress=true`。

将下一关改为 1-4 的参数示例：

```json
{
  "level":4,
  "expected_current_level":5,
  "expected_save_sha256":"从刚才读取结果复制真实哈希",
  "expected_session_id":"从刚才读取结果复制真实身份",
  "command_id":"本次修改独有的标识"
}
```

`expected_current_level`、哈希和进程身份都必须来自最新读取结果。写入前会检查当前适配构建的 v12、892 字节存档布局，并备份到 `data/backup`。没有变化时返回 `changed:false`、`admin_used:false`、`backup:null`。

修改后再次调用 `pvz_get_save_info` 复核。该接口只修改当前已加载用户的冒险进度，不修改金币、植物解锁或其他任意字段。

如果返回 `SAVE_CHANGED`、`PROGRESS_CHANGED` 或 `STALE_SESSION`，应重新获取状态和校验值；不要使用旧参数强行重试。
