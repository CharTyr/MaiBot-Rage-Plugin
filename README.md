# 🔥 麦麦哈气插件 (MaiBot Rage Plugin)

为MaiBot加入可量化的怒气值系统，通过Action让planner智能判断挑衅行为！

## ✨ 功能特性

- **智能检测**: 由planner智能判断挑衅/调戏/烦人行为，而非简单关键词匹配
- **怒气值追踪**: 每个群聊独立追踪怒气值（0-100）
- **三档怒气等级**: 
  - Lv.0 😊 心平气和
  - Lv.1 😤 轻微不爽（30+怒气）
  - Lv.2 😠 明显生气（60+怒气）
  - Lv.3 🤬 暴怒中（85+怒气）
- **动态Prompt注入**: 根据怒气等级自动调整回复风格
- **自然衰减**: 怒气值会随时间自然降低

## 🎯 工作原理

插件提供三个Action供planner选择：

| Action | 触发场景 | 怒气增加 |
|--------|----------|----------|
| `rage_provocation` | 挑衅、辱骂、攻击 | 8/18/35（轻/中/重） |
| `rage_tease` | 调戏、撩、土味情话 | 5 |
| `rage_annoy` | 烦人、纠缠、骚扰 | 10 |

当planner判断用户行为符合上述场景时，会自动选择对应Action，增加怒气值。

## 🎮 命令

| 命令 | 说明 |
|------|------|
| `/rage show` | 查看当前怒气状态 |
| `/rage set <数值>` | 设置怒气值 |
| `/rage reset` | 重置怒气值 |

## ⚙️ 配置

编辑 `config.toml` 自定义：

```toml
[rage]
provocation_mild = 8.0      # 轻度挑衅增加怒气
provocation_moderate = 18.0  # 中度挑衅增加怒气
provocation_severe = 35.0    # 重度挑衅增加怒气
tease_amount = 5.0           # 调戏增加怒气
annoy_amount = 10.0          # 烦人增加怒气

[rage.levels]
level1_threshold = 30.0  # 轻微不爽阈值
level2_threshold = 60.0  # 明显生气阈值
level3_threshold = 85.0  # 暴怒阈值
```

## 📝 License

GPL-v3.0-or-later
