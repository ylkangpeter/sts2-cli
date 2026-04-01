#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP游戏服务测试客户端

演示如何使用HTTP API与游戏交互
"""

import time

import requests

BASE_URL = "http://localhost:5000"


def test_http_game_service():
    """测试HTTP游戏服务"""
    
    print("=" * 50)
    print("HTTP游戏服务测试")
    print("=" * 50)
    
    # 1. 检查服务健康状态
    print("\n1. 检查服务健康状态...")
    try:
        response = requests.get(f"{BASE_URL}/health")
        print(f"   状态: {response.json()}")
    except Exception as e:
        print(f"   错误: {e}")
        print("   请先运行: python python/http_game_service.py")
        return
    
    # 2. 启动新游戏
    print("\n2. 启动新游戏...")
    start_data = {
        "character": "Ironclad",
        "seed": "http_test",
        "game_dir": "C:\\path\\to\\SlayTheSpire2"  # 如需手动指定，改成你自己的路径
    }
    
    response = requests.post(f"{BASE_URL}/start", json=start_data)
    result = response.json()
    
    if result["status"] != "success":
        print(f"   启动失败: {result}")
        return
    
    game_id = result["game_id"]
    print(f"   游戏ID: {game_id}")
    print(f"   角色: {result['character']}")
    print(f"   种子: {result['seed']}")
    
    # 3. 获取初始状态
    print("\n3. 获取初始状态...")
    response = requests.get(f"{BASE_URL}/state/{game_id}")
    state_data = response.json()
    
    if state_data["status"] != "success":
        print(f"   获取状态失败: {state_data}")
        return
    
    state = state_data["state"]
    print(f"   决策类型: {state.get('decision')}")
    print(f"   回合: {state.get('turn')}")
    print(f"   能量: {state.get('energy')}")
    print(f"   手牌数量: {len(state.get('hand', []))}")
    
    # 4. 执行几个动作
    print("\n4. 执行动作...")
    total_reward = 0
    step = 0
    
    try:
        while step < 20:  # 限制步数用于测试
            # 获取当前状态
            response = requests.get(f"{BASE_URL}/state/{game_id}")
            state_data = response.json()
            
            if state_data["status"] != "success":
                print(f"   获取状态失败: {state_data}")
                break
            
            state = state_data["state"]
            decision = state.get("decision")
            
            # 根据决策类型选择动作
            if decision == "combat_play":
                # 简单的战斗逻辑：随机选择一张可玩的卡牌
                hand = state.get("hand", [])
                energy = state.get("energy", 0)
                playable = [c for c in hand if c.get("can_play") and c.get("cost", 99) <= energy]
                
                if playable:
                    import random
                    card = random.choice(playable)
                    action = {
                        "cmd": "action",
                        "action": "play_card",
                        "args": {"card_index": card["index"]}
                    }
                    
                    # 如果需要目标
                    if card.get("target_type") == "AnyEnemy":
                        enemies = state.get("enemies", [])
                        if enemies:
                            target = random.choice(enemies)
                            action["args"]["target_index"] = target["index"]
                else:
                    action = {
                        "cmd": "action",
                        "action": "end_turn"
                    }
            
            elif decision == "map_node":
                # 随机选择一个节点
                choices = state.get("choices", [])
                if choices:
                    import random
                    choice = random.choice(choices)
                    action = {
                        "cmd": "action",
                        "action": "select_map_node",
                        "args": {"col": choice["col"], "row": choice["row"]}
                    }
                else:
                    action = {"cmd": "action", "action": "end_turn"}
            
            elif decision == "card_reward":
                # 随机选择一张卡牌
                cards = state.get("cards", [])
                if cards:
                    import random
                    card_idx = random.randint(0, len(cards) - 1)
                    action = {
                        "cmd": "action",
                        "action": "choose_card",
                        "args": {"card_index": card_idx}
                    }
                else:
                    action = {"cmd": "action", "action": "end_turn"}
            
            else:
                # 默认动作
                action = {"cmd": "action", "action": "end_turn"}
            
            # 执行动作
            response = requests.post(f"{BASE_URL}/step/{game_id}", json=action)
            step_result = response.json()
            
            if step_result["status"] != "success":
                print(f"   执行动作失败: {step_result}")
                break
            
            new_state = step_result["state"]
            reward = step_result["reward"]
            game_over = step_result["game_over"]
            
            total_reward += reward
            step += 1
            
            print(f"   步骤 {step}: 奖励 {reward:.2f}, 总奖励 {total_reward:.2f}")
            
            if game_over:
                print(f"   游戏结束！胜利: {step_result['victory']}")
                break
            
            time.sleep(0.1)  # 稍微延迟，避免过快请求
    
    except KeyboardInterrupt:
        print("\n   用户中断")
    except Exception as e:
        print(f"\n   错误: {e}")
    
    # 5. 关闭游戏
    print("\n5. 关闭游戏...")
    response = requests.post(f"{BASE_URL}/close/{game_id}")
    result = response.json()
    print(f"   {result}")
    
    # 6. 列出所有游戏
    print("\n6. 列出所有游戏...")
    response = requests.get(f"{BASE_URL}/games")
    games_data = response.json()
    print(f"   活跃游戏数量: {len(games_data['games'])}")
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    test_http_game_service()
