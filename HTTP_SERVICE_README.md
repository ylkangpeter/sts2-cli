# HTTP游戏服务使用说明

## 启动服务

```bash
python python/http_game_service.py
```

服务将在 `http://localhost:5000` 启动。

## API接口

### 1. 启动新游戏
```bash
POST http://localhost:5000/start
Content-Type: application/json

{
  "character": "Ironclad",
  "seed": "test",
  "game_dir": "C:\\path\\to\\SlayTheSpire2"
}
```

响应：
```json
{
  "status": "success",
  "game_id": "test_1234567890",
  "character": "Ironclad",
  "seed": "test"
}
```

### 2. 获取游戏状态
```bash
GET http://localhost:5000/state/{game_id}
```

响应：
```json
{
  "status": "success",
  "state": {
    "decision": "combat_play",
    "turn": 1,
    "energy": 3,
    "hand": [...],
    "enemies": [...],
    "player": {...}
  }
}
```

### 3. 执行动作
```bash
POST http://localhost:5000/step/{game_id}
Content-Type: application/json

{
  "cmd": "action",
  "action": "play_card",
  "args": {
    "card_index": 0,
    "target_index": 0
  }
}
```

响应：
```json
{
  "status": "success",
  "state": {...},
  "reward": 0.1,
  "game_over": false,
  "victory": false
}
```

### 4. 关闭游戏
```bash
POST http://localhost:5000/close/{game_id}
```

响应：
```json
{
  "status": "success",
  "message": "Game closed"
}
```

### 5. 列出所有游戏
```bash
GET http://localhost:5000/games
```

响应：
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

### 6. 健康检查
```bash
GET http://localhost:5000/health
```

响应：
```json
{
  "status": "healthy",
  "active_games": 1
}
```

## Python客户端示例

```python
import requests

BASE_URL = "http://localhost:5000"

# 启动游戏
response = requests.post(f"{BASE_URL}/start", json={
    "character": "Ironclad",
    "seed": "test",
    "game_dir": "C:\\path\\to\\SlayTheSpire2"
})
game_id = response.json()["game_id"]

# 获取状态
response = requests.get(f"{BASE_URL}/state/{game_id}")
state = response.json()["state"]

# 执行动作
response = requests.post(f"{BASE_URL}/step/{game_id}", json={
    "cmd": "action",
    "action": "play_card",
    "args": {"card_index": 0, "target_index": 0}
})
result = response.json()
new_state = result["state"]
reward = result["reward"]

# 关闭游戏
requests.post(f"{BASE_URL}/close/{game_id}")
```

## 测试客户端

运行测试客户端：
```bash
python python/test_http_service.py
```

## 强化学习集成

```python
import requests

class RLAgent:
    def __init__(self, game_id):
        self.game_id = game_id
        self.base_url = "http://localhost:5000"
    
    def get_state(self):
        response = requests.get(f"{self.base_url}/state/{self.game_id}")
        return response.json()["state"]
    
    def step(self, action):
        response = requests.post(f"{self.base_url}/step/{self.game_id}", json=action)
        result = response.json()
        return result["state"], result["reward"]
    
    def act(self, state):
        # 你的强化学习逻辑
        decision = state.get("decision")
        
        if decision == "combat_play":
            hand = state.get("hand", [])
            energy = state.get("energy", 0)
            playable = [c for c in hand if c.get("can_play") and c.get("cost", 99) <= energy]
            
            if playable:
                card = your_model.select_card(playable, state)
                action = {
                    "cmd": "action",
                    "action": "play_card",
                    "args": {"card_index": card["index"]}
                }
                
                if card.get("target_type") == "AnyEnemy":
                    enemies = state.get("enemies", [])
                    if enemies:
                        target = your_model.select_target(enemies, state)
                        action["args"]["target_index"] = target["index"]
                
                return action
            else:
                return {"cmd": "action", "action": "end_turn"}
        
        return {"cmd": "action", "action": "end_turn"}

# 使用
agent = RLAgent("test_1234567890")
state = agent.get_state()
action = agent.act(state)
new_state, reward = agent.step(action)
```

## 特点

1. **无继承**：不需要继承任何类，直接通过HTTP API调用
2. **多游戏支持**：可以同时运行多个游戏实例
3. **跨平台**：任何支持HTTP的编程语言都可以使用
4. **实时交互**：通过REST API实时获取状态和执行动作
5. **自动奖励计算**：自动计算每步的奖励值
