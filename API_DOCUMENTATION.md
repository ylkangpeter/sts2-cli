# HTTP游戏服务完整API文档

## 目录
1. [概述](#概述)
2. [API端点](#api端点)
3. [游戏状态](#游戏状态)
4. [决策类型](#决策类型)
5. [动作类型](#动作类型)
6. [数据模型](#数据模型)
7. [错误处理](#错误处理)
8. [示例](#示例)

---

## 概述

HTTP游戏服务提供REST API接口来控制Slay the Spire 2游戏。通过HTTP请求可以实现：
- 启动和管理多个游戏实例
- 获取完整的游戏状态
- 执行游戏动作
- 接收奖励和游戏结果

**基础URL**: `http://localhost:5000`

**数据格式**: JSON

**字符编码**: UTF-8

---

## API端点

### 1. 启动新游戏

**端点**: `POST /start`

**请求体**:
```json
{
  "character": "Ironclad",      // 必需，角色名称
  "seed": "test",              // 可选，随机种子
  "game_dir": "C:\\path\\to\\SlayTheSpire2"  // 可选，游戏目录路径
}
```

**角色选项**:
- `Ironclad` - 铁甲战士
- `Silent` - 寂静者
- `Defect` - 缺陷
- `Regent` - 摄政王
- `Necrobinder` - 死灵师

**响应**:
```json
{
  "status": "success",           // 成功状态
  "game_id": "test_1234567890", // 游戏唯一标识符
  "character": "Ironclad",       // 角色名称
  "seed": "test"               // 使用的种子
}
```

**错误响应**:
```json
{
  "status": "error",
  "message": "未找到 .NET SDK，请安装 .NET 9+"
}
```

---

### 2. 获取游戏状态

**端点**: `GET /state/{game_id}`

**路径参数**:
- `game_id` - 游戏唯一标识符

**响应**:
```json
{
  "status": "success",
  "state": {
    // 完整的游戏状态对象，详见[游戏状态](#游戏状态)
  }
}
```

**错误响应**:
```json
{
  "status": "error",
  "message": "Game not found"
}
```

---

### 3. 执行动作

**端点**: `POST /step/{game_id}`

**路径参数**:
- `game_id` - 游戏唯一标识符

**请求体**:
```json
{
  "cmd": "action",              // 命令类型，通常为"action"
  "action": "play_card",        // 动作类型，详见[动作类型](#动作类型)
  "args": {                    // 动作参数
    "card_index": 0,
    "target_index": 0
  }
}
```

**响应**:
```json
{
  "status": "success",
  "state": {                   // 新的游戏状态
    // 详见[游戏状态](#游戏状态)
  },
  "reward": 0.1,               // 奖励值
  "game_over": false,           // 游戏是否结束
  "victory": false             // 是否胜利
}
```

---

### 4. 关闭游戏

**端点**: `POST /close/{game_id}`

**路径参数**:
- `game_id` - 游戏唯一标识符

**响应**:
```json
{
  "status": "success",
  "message": "Game closed"
}
```

---

### 5. 列出所有游戏

**端点**: `GET /games`

**响应**:
```json
{
  "status": "success",
  "games": [
    {
      "game_id": "test_1234567890",
      "character": "Ironclad",
      "seed": "test",
      "alive": true,
      "created_at": "2026-03-29T20:00:00"
    }
  ]
}
```

---

### 6. 健康检查

**端点**: `GET /health`

**响应**:
```json
{
  "status": "healthy",
  "active_games": 1
}
```

---

## 游戏状态

游戏状态是一个复杂的JSON对象，包含所有游戏信息。根据当前决策类型，状态的结构会有所不同。

### 通用字段

所有状态都包含以下字段：

```json
{
  "decision": "combat_play",  // 当前决策类型
  "game_over": false,        // 游戏是否结束
  "victory": false          // 是否胜利
}
```

---

## 决策类型

### 1. combat_play - 战斗决策

当玩家在战斗中时，决策类型为 `combat_play`。

**状态字段**:
```json
{
  "decision": "combat_play",
  "turn": 1,                    // 当前回合数
  "energy": 3,                  // 当前能量
  "hand": [...],                 // 手牌数组，详见[手牌](#手牌)
  "enemies": [...],              // 敌人数组，详见[敌人](#敌人)
  "player": {...},               // 玩家信息，详见[玩家](#玩家)
  "player_powers": [...],         // 玩家能力数组
  "draw_pile_count": 4,          // 抽牌堆数量
  "discard_pile_count": 1,       // 弃牌堆数量
  "exhaust_pile_count": 0        // 消耗牌堆数量
}
```

**可用动作**:
- `play_card` - 出牌
- `end_turn` - 结束回合
- `use_potion` - 使用药水

---

### 2. map_node - 地图节点决策

当玩家在地图上选择路径时，决策类型为 `map_node`。

**状态字段**:
```json
{
  "decision": "map_node",
  "map": [...],                 // 地图节点数组
  "boss": {...},                // Boss信息
  "current_coord": {...},        // 当前坐标
  "player": {...}               // 玩家信息
}
```

**地图节点结构**:
```json
{
  "col": 3,                    // 列坐标 (0-6)
  "row": 1,                    // 行坐标 (0-16)
  "type": "Monster",            // 节点类型
  "children": [...],            // 子节点数组
  "visited": false,             // 是否已访问
  "current": false             // 是否为当前节点
}
```

**节点类型**:
- `Monster` - 普通怪物
- `Elite` - 精英怪物
- `RestSite` - 休息点
- `Shop` - 商店
- `Treasure` - 宝箱
- `Unknown` - 未知（可能是事件）
- `Boss` - Boss

**可用动作**:
- `select_map_node` - 选择地图节点

---

### 3. card_reward - 卡牌奖励决策

当玩家获得卡牌奖励时，决策类型为 `card_reward`。

**状态字段**:
```json
{
  "decision": "card_reward",
  "cards": [...],               // 可选卡牌数组
  "can_skip": true,             // 是否可以跳过
  "player": {...}               // 玩家信息
}
```

**可用动作**:
- `choose_card` - 选择卡牌
- `skip_reward` - 跳过奖励

---

### 4. shop - 商店决策

当玩家在商店时，决策类型为 `shop`。

**状态字段**:
```json
{
  "decision": "shop",
  "cards": [...],               // 可购买卡牌数组
  "potions": [...],            // 可购买药水数组
  "relics": [...],             // 可购买遗物数组
  "purge_cost": 50,           // 移除卡牌费用
  "player": {...}               // 玩家信息
}
```

**可用动作**:
- `buy_card` - 购买卡牌
- `buy_potion` - 购买药水
- `buy_relic` - 购买遗物
- `purge_card` - 移除卡牌
- `leave_shop` - 离开商店

---

### 5. event - 事件决策

当玩家遇到随机事件时，决策类型为 `event`。

**状态字段**:
```json
{
  "decision": "event",
  "event_id": "BIG_FISH",     // 事件ID
  "event_name": "大鱼",       // 事件名称
  "description": "...",        // 事件描述
  "choices": [...],          // 可选选项
  "player": {...}           // 玩家信息
}
```

**选项结构**:
```json
{
  "index": 0,                 // 选项索引
  "text": "吃掉这条鱼",      // 选项文本
  "description": "..."        // 选项描述
}
```

**可用动作**:
- `choose_event_option` - 选择事件选项

---

## 动作类型

### 1. play_card - 出牌

**请求**:
```json
{
  "cmd": "action",
  "action": "play_card",
  "args": {
    "card_index": 0,          // 必需，手牌索引
    "target_index": 0          // 可选，目标索引（当卡牌需要目标时）
  }
}
```

**说明**:
- `card_index`: 手牌中卡牌的索引
- `target_index`: 当卡牌的 `target_type` 为 `AnyEnemy` 时需要指定目标

---

### 2. end_turn - 结束回合

**请求**:
```json
{
  "cmd": "action",
  "action": "end_turn"
}
```

**说明**:
- 结束当前回合，敌人会执行其意图

---

### 3. use_potion - 使用药水

**请求**:
```json
{
  "cmd": "action",
  "action": "use_potion",
  "args": {
    "potion_index": 0,        // 必需，药水索引
    "target_index": 0          // 可选，目标索引
  }
}
```

---

### 4. select_map_node - 选择地图节点

**请求**:
```json
{
  "cmd": "action",
  "action": "select_map_node",
  "args": {
    "col": 3,                 // 必需，列坐标
    "row": 1                  // 必需，行坐标
  }
}
```

---

### 5. choose_card - 选择卡牌

**请求**:
```json
{
  "cmd": "action",
  "action": "choose_card",
  "args": {
    "card_index": 0           // 必需，卡牌索引
  }
}
```

---

### 6. buy_card - 购买卡牌

**请求**:
```json
{
  "cmd": "action",
  "action": "buy_card",
  "args": {
    "card_index": 0           // 必需，卡牌索引
  }
}
```

---

### 7. buy_potion - 购买药水

**请求**:
```json
{
  "cmd": "action",
  "action": "buy_potion",
  "args": {
    "potion_index": 0         // 必需，药水索引
  }
}
```

---

### 8. buy_relic - 购买遗物

**请求**:
```json
{
  "cmd": "action",
  "action": "buy_relic",
  "args": {
    "relic_index": 0          // 必需，遗物索引
  }
}
```

---

### 9. purge_card - 移除卡牌

**请求**:
```json
{
  "cmd": "action",
  "action": "purge_card",
  "args": {
    "card_index": 0           // 必需，卡牌索引
  }
}
```

---

### 10. choose_event_option - 选择事件选项

**请求**:
```json
{
  "cmd": "action",
  "action": "choose_event_option",
  "args": {
    "option_index": 0         // 必需，选项索引
  }
}
```

---

## 数据模型

### 手牌

```json
{
  "index": 0,                    // 手牌索引
  "id": "CARD.STRIKE_IRONCLAD", // 卡牌唯一标识符
  "name": "打击",               // 卡牌名称
  "cost": 1,                   // 能量费用
  "type": "Attack",             // 卡牌类型
  "can_play": true,             // 是否可以打出
  "target_type": "AnyEnemy",     // 目标类型
  "upgraded": false,            // 是否已升级
  "description": "造成6点伤害。", // 卡牌描述
  "stats": {                   // 卡牌数值
    "damage": 6,
    "block": 5,
    "magic_number": 3
  },
  "keywords": null,             // 关键词数组
  "after_upgrade": {           // 升级后的属性
    "cost": 1,
    "stats": {
      "damage": 9
    },
    "description": "造成9点伤害。",
    "added_keywords": null,
    "removed_keywords": null
  }
}
```

**卡牌类型**:
- `Attack` - 攻击卡
- `Skill` - 技能卡
- `Power` - 能力卡
- `Status` - 状态卡
- `Curse` - 诅咒卡

**目标类型**:
- `AnyEnemy` - 任意敌人
- `AllEnemies` - 所有敌人
- `Self` - 自己
- `None` - 无目标

---

### 敌人

```json
{
  "index": 0,                    // 敌人索引
  "id": "GROWLER",            // 敌人唯一标识符
  "name": "小啃兽",           // 敌人名称
  "hp": 45,                    // 当前生命值
  "max_hp": 45,                // 最大生命值
  "block": 0,                  // 当前格挡
  "intends_attack": true,        // 是否计划攻击
  "intents": [...],             // 意图数组
  "powers": [...]               // 能力数组
}
```

**意图类型**:
- `Attack` - 攻击
- `Defend` - 防御
- `Buff` - 增益
- `Debuff` - 减益
- `Unknown` - 未知

**意图结构**:
```json
{
  "type": "Attack",
  "damage": 12,
  "block": 5,
  "count": 2
}
```

---

### 玩家

```json
{
  "name": "铁甲战士",           // 角色名称
  "hp": 80,                    // 当前生命值
  "max_hp": 80,                // 最大生命值
  "block": 0,                  // 当前格挡
  "gold": 99,                  // 金币数量
  "relics": [...],             // 遗物数组
  "potions": [...],            // 药水数组
  "deck_size": 9,             // 牌组大小
  "deck": [...]                // 完整牌组
}
```

**遗物结构**:
```json
{
  "name": "燃烧之血",           // 遗物名称
  "description": "战斗结束时，回复{Heal}点生命。", // 描述
  "vars": {                   // 变量
    "Heal": 6
  }
}
```

**药水结构**:
```json
{
  "index": 0,                    // 药水索引
  "name": "敏捷药水",          // 药水名称
  "description": "获得{DexterityPower}点敏捷。下个回合结束时，失去{DexterityPower}点敏捷。", // 描述
  "vars": {                   // 变量
    "DexterityPower": 5
  },
  "target_type": "AnyPlayer"    // 目标类型
}
```

---

## 错误处理

### 错误响应格式

所有错误响应都遵循以下格式：

```json
{
  "status": "error",
  "message": "错误描述"
}
```

### 常见错误

| HTTP状态码 | 错误消息 | 说明 |
|-----------|----------|------|
| 404 | "Game not found" | 游戏ID不存在 |
| 400 | "Game process is not alive" | 游戏进程已终止 |
| 400 | "Game state is not available" | 游戏状态不可用 |
| 400 | "No action provided" | 未提供动作 |
| 500 | "未找到 .NET SDK，请安装 .NET 9+" | 系统错误 |

---

## 示例

### 完整游戏流程

```python
import requests

BASE_URL = "http://localhost:5000"

# 1. 启动游戏
response = requests.post(f"{BASE_URL}/start", json={
    "character": "Ironclad",
    "seed": "example",
    "game_dir": "C:\\path\\to\\SlayTheSpire2"
})
game_id = response.json()["game_id"]
print(f"游戏已启动: {game_id}")

# 2. 游戏循环
total_reward = 0
while True:
    # 获取状态
    response = requests.get(f"{BASE_URL}/state/{game_id}")
    state = response.json()["state"]
    
    # 检查游戏是否结束
    if state.get("game_over"):
        print(f"游戏结束！胜利: {state.get('victory')}")
        print(f"总奖励: {total_reward}")
        break
    
    # 根据决策类型选择动作
    decision = state.get("decision")
    
    if decision == "combat_play":
        # 战斗逻辑
        hand = state.get("hand", [])
        energy = state.get("energy", 0)
        
        # 找出可玩的卡牌
        playable = [c for c in hand if c.get("can_play") and c.get("cost", 99) <= energy]
        
        if playable:
            # 选择第一张可玩的卡牌
            card = playable[0]
            action = {
                "cmd": "action",
                "action": "play_card",
                "args": {"card_index": card["index"]}
            }
            
            # 如果需要目标
            if card.get("target_type") == "AnyEnemy":
                enemies = state.get("enemies", [])
                if enemies:
                    # 选择生命值最低的敌人
                    target = min(enemies, key=lambda e: e.get("hp", 999))
                    action["args"]["target_index"] = target["index"]
        else:
            action = {"cmd": "action", "action": "end_turn"}
    
    elif decision == "map_node":
        # 地图选择逻辑
        choices = state.get("choices", [])
        if choices:
            # 选择第一个节点
            choice = choices[0]
            action = {
                "cmd": "action",
                "action": "select_map_node",
                "args": {"col": choice["col"], "row": choice["row"]}
            }
        else:
            action = {"cmd": "action", "action": "end_turn"}
    
    elif decision == "card_reward":
        # 卡牌奖励逻辑
        cards = state.get("cards", [])
        if cards:
            # 选择第一张卡牌
            action = {
                "cmd": "action",
                "action": "choose_card",
                "args": {"card_index": 0}
            }
        else:
            action = {"cmd": "action", "action": "skip_reward"}
    
    else:
        # 默认动作
        action = {"cmd": "action", "action": "end_turn"}
    
    # 执行动作
    response = requests.post(f"{BASE_URL}/step/{game_id}", json=action)
    result = response.json()
    
    # 更新奖励
    reward = result["reward"]
    total_reward += reward
    
    print(f"决策: {decision}, 奖励: {reward:.2f}, 总奖励: {total_reward:.2f}")

# 3. 关闭游戏
requests.post(f"{BASE_URL}/close/{game_id}")
print("游戏已关闭")
```

---

## 附录

### 状态转换图

```
map_node -> combat_play -> combat_play -> ... -> map_node
         -> card_reward -> map_node
         -> shop -> map_node
         -> event -> map_node
         -> rest_site -> map_node
```

### 常用字段速查

| 字段 | 类型 | 说明 | 示例值 |
|------|------|------|--------|
| decision | string | 当前决策类型 | "combat_play" |
| turn | int | 当前回合数 | 1 |
| energy | int | 当前能量 | 3 |
| hp | int | 当前生命值 | 80 |
| max_hp | int | 最大生命值 | 80 |
| block | int | 当前格挡 | 5 |
| gold | int | 金币数量 | 99 |
| game_over | bool | 游戏是否结束 | false |
| victory | bool | 是否胜利 | false |
| reward | float | 动作奖励 | 0.1 |

---

**文档版本**: 1.0
**最后更新**: 2026-03-29
**API版本**: 0.2.0
