"""
麦麦哈气插件 (MaiBot Rage Plugin)
为MaiBot加入可量化的怒气值系统
通过Action让planner智能判断挑衅/调戏行为
"""

import time
import asyncio
from typing import List, Tuple, Type, Optional, Dict, Any
from dataclasses import dataclass, field

from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseAction,
    BaseCommand,
    BaseEventHandler,
    ComponentInfo,
    ConfigField,
    ActionActivationType,
    EventType,
    MaiMessages,
)
from src.plugin_system.apis import send_api
from src.common.logger import get_logger

logger = get_logger("RagePlugin")


@dataclass
class RageState:
    """怒气值状态"""
    value: float = 0.0
    last_update: float = field(default_factory=time.time)
    level: int = 0  # 0=正常, 1=轻微不爽, 2=明显生气, 3=暴怒


class RageManager:
    """怒气值管理器 - 单例模式"""
    _instance: Optional["RageManager"] = None
    _rage_states: Dict[str, RageState] = {}
    _config: Dict[str, Any] = {}
    _decay_task: Optional[asyncio.Task] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def set_config(self, config: Dict[str, Any]):
        """设置配置"""
        self._config = config

    def get_rage(self, chat_id: str) -> RageState:
        """获取指定聊天的怒气状态"""
        if chat_id not in self._rage_states:
            self._rage_states[chat_id] = RageState()
        return self._rage_states[chat_id]

    def add_rage(self, chat_id: str, amount: float) -> RageState:
        """增加怒气值"""
        state = self.get_rage(chat_id)
        max_rage = self._config.get("rage", {}).get("max_rage", 100.0)
        state.value = min(state.value + amount, max_rage)
        state.last_update = time.time()
        state.level = self._calculate_level(state.value)
        logger.info(f"[Rage] chat_id={chat_id} 怒气值+{amount:.1f} -> {state.value:.1f} (等级{state.level})")
        return state

    def set_rage(self, chat_id: str, value: float) -> RageState:
        """设置怒气值"""
        state = self.get_rage(chat_id)
        max_rage = self._config.get("rage", {}).get("max_rage", 100.0)
        state.value = max(0, min(value, max_rage))
        state.last_update = time.time()
        state.level = self._calculate_level(state.value)
        return state

    def reset_rage(self, chat_id: str) -> RageState:
        """重置怒气值"""
        state = self.get_rage(chat_id)
        state.value = 0.0
        state.last_update = time.time()
        state.level = 0
        return state

    def decay_rage(self, chat_id: str, elapsed_seconds: Optional[float] = None) -> RageState:
        """衰减怒气值

        配置中的 decay_rate 表示“每分钟衰减值”，因此会按 elapsed_seconds 进行折算。
        """
        state = self.get_rage(chat_id)
        if state.value <= 0:
            return state
        
        decay_rate_per_min = float(self._config.get("rage", {}).get("decay_rate", 0.5))
        if elapsed_seconds is None:
            elapsed_seconds = float(self._config.get("rage", {}).get("decay_interval", 60))
        decay_amount = decay_rate_per_min * (float(elapsed_seconds) / 60.0)
        state.value = max(0, state.value - decay_amount)
        state.last_update = time.time()
        state.level = self._calculate_level(state.value)
        return state

    def _calculate_level(self, value: float) -> int:
        """计算怒气等级"""
        levels = self._config.get("rage", {}).get("levels", {})
        level3 = levels.get("level3_threshold", 85.0)
        level2 = levels.get("level2_threshold", 60.0)
        level1 = levels.get("level1_threshold", 30.0)
        
        if value >= level3:
            return 3
        elif value >= level2:
            return 2
        elif value >= level1:
            return 1
        return 0

    def get_rage_prompt(self, chat_id: str) -> str:
        """获取当前怒气等级对应的prompt"""
        state = self.get_rage(chat_id)
        if state.level == 0:
            return ""
        
        prompts = self._config.get("prompts", {})
        prompt_key = f"level{state.level}"
        return prompts.get(prompt_key, "")

    async def start_decay_loop(self):
        """启动怒气衰减循环"""
        if self._decay_task is not None:
            return
        
        async def decay_loop():
            while True:
                interval = self._config.get("rage", {}).get("decay_interval", 60)
                await asyncio.sleep(interval)
                
                if not self._config.get("features", {}).get("enable_decay", True):
                    continue
                
                for chat_id in list(self._rage_states.keys()):
                    self.decay_rage(chat_id, elapsed_seconds=interval)
        
        self._decay_task = asyncio.create_task(decay_loop())
        logger.info("[Rage] 怒气衰减循环已启动")


# 全局怒气管理器实例
rage_manager = RageManager()

# 插件启用状态
_enabled_chats: Dict[str, bool] = {}  # chat_id -> enabled
_global_enabled: bool = True  # 全局开关


def is_plugin_enabled(chat_id: str) -> bool:
    """检查插件在指定聊天中是否启用"""
    if not _global_enabled:
        return False
    return _enabled_chats.get(chat_id, True)


def set_plugin_enabled(chat_id: str, enabled: bool):
    """设置插件在指定聊天中的启用状态"""
    _enabled_chats[chat_id] = enabled


def set_global_enabled(enabled: bool):
    """设置全局启用状态"""
    global _global_enabled
    _global_enabled = enabled


def is_global_enabled() -> bool:
    """获取全局启用状态"""
    return _global_enabled


def check_admin_permission(user_id: str, config: Dict[str, Any]) -> bool:
    """检查用户是否有管理员权限"""
    admin_list = config.get("auth", {}).get("admin_qq", [])
    if not admin_list:
        return True  # 未配置则所有人可用
    return str(user_id) in [str(qq) for qq in admin_list]


# ===== Action组件 - 由Planner智能判断 =====

class ProvocationAction(BaseAction):
    """挑衅检测Action - 当检测到挑衅行为时由planner选择"""
    
    action_name = "rage_provocation"
    action_description = "检测到对方在挑衅、辱骂、攻击你，增加怒气值"
    
    # 使用LLM_JUDGE让planner智能判断
    activation_type = ActionActivationType.LLM_JUDGE
    
    # 可以和回复并行执行
    parallel_action = True
    
    # 动作参数
    action_parameters = {
        "intensity": "挑衅强度：mild(轻微)/moderate(中度)/severe(严重)"
    }
    
    # 告诉planner什么时候应该选择这个action
    action_require = [
        "当有人在骂你、侮辱你、攻击你时使用",
        "当有人说脏话、人身攻击时使用",
        "当有人用恶意言语挑衅你时使用",
        "当有人嘲讽、贬低、看不起你时使用",
        "当有人故意激怒你、找茬时使用",
    ]
    
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行挑衅检测 - 增加怒气值"""
        chat_id = self.chat_stream.stream_id if self.chat_stream else None
        if not chat_id:
            return False, "无法获取聊天信息"
        
        # 检查插件是否启用
        if not is_plugin_enabled(chat_id):
            return True, "插件已禁用"
        
        # 获取挑衅强度
        intensity = self.action_data.get("intensity", "moderate")
        
        # 根据强度增加不同的怒气值
        rage_amounts = {
            "mild": self.get_config("rage.provocation_mild", 8.0),
            "moderate": self.get_config("rage.provocation_moderate", 18.0),
            "severe": self.get_config("rage.provocation_severe", 35.0),
        }
        
        amount = rage_amounts.get(intensity, 18.0)
        state = rage_manager.add_rage(chat_id, amount)
        
        logger.info(f"[Rage] 检测到挑衅行为(强度:{intensity})，怒气+{amount} -> {state.value:.1f}")
        
        return True, f"检测到挑衅，怒气值增加{amount:.0f}点"


class TeaseAction(BaseAction):
    """调戏检测Action - 当检测到调戏行为时由planner选择"""
    
    action_name = "rage_tease"
    action_description = "检测到对方在调戏、撩你、说土味情话，轻微增加怒气值"
    
    activation_type = ActionActivationType.LLM_JUDGE
    parallel_action = True
    
    action_parameters = {}
    
    action_require = [
        "当有人在调戏你、撩你时使用",
        "当有人叫你老婆、宝贝等亲密称呼时使用",
        "当有人对你说土味情话、表白时使用",
        "当有人要求你做亲密动作（亲亲、抱抱）时使用",
        "当有人用暧昧的方式和你说话时使用",
    ]
    
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行调戏检测 - 轻微增加怒气值"""
        chat_id = self.chat_stream.stream_id if self.chat_stream else None
        if not chat_id:
            return False, "无法获取聊天信息"
        
        if not is_plugin_enabled(chat_id):
            return True, "插件已禁用"
        
        amount = self.get_config("rage.tease_amount", 5.0)
        state = rage_manager.add_rage(chat_id, amount)
        
        logger.info(f"[Rage] 检测到调戏行为，怒气+{amount} -> {state.value:.1f}")
        
        return True, f"被调戏了，怒气值增加{amount:.0f}点"


class AnnoyAction(BaseAction):
    """烦人检测Action - 当检测到烦人行为时由planner选择"""
    
    action_name = "rage_annoy"
    action_description = "检测到对方在烦你、纠缠你、重复骚扰，增加怒气值"
    
    activation_type = ActionActivationType.LLM_JUDGE
    parallel_action = True
    
    action_parameters = {}
    
    action_require = [
        "当有人反复问同样的问题烦你时使用",
        "当有人一直纠缠不放时使用",
        "当有人故意捣乱、刷屏时使用",
        "当有人的行为让你感到厌烦时使用",
        "当有人不停地@你、打扰你时使用",
    ]
    
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行烦人检测 - 增加怒气值"""
        chat_id = self.chat_stream.stream_id if self.chat_stream else None
        if not chat_id:
            return False, "无法获取聊天信息"
        
        if not is_plugin_enabled(chat_id):
            return True, "插件已禁用"
        
        amount = self.get_config("rage.annoy_amount", 10.0)
        state = rage_manager.add_rage(chat_id, amount)
        
        logger.info(f"[Rage] 检测到烦人行为，怒气+{amount} -> {state.value:.1f}")
        
        return True, f"被烦到了，怒气值增加{amount:.0f}点"


# ===== EventHandler - Prompt注入 =====

class RagePromptInjector(BaseEventHandler):
    """怒气Prompt注入器 - 在LLM请求前注入怒气prompt"""
    
    event_type = EventType.POST_LLM
    handler_name = "rage_prompt_injector"
    handler_description = "根据怒气等级注入对应的prompt"
    
    # 必须设置为True才能阻塞执行并返回修改后的消息
    intercept_message = True

    async def execute(self, message: MaiMessages | None) -> Tuple[bool, bool, str | None, None, Optional[MaiMessages]]:
        """在LLM请求前注入怒气prompt"""
        if not message:
            return True, True, None, None, None
        
        # MaiMessages使用stream_id而不是chat_stream
        chat_id = message.stream_id
        if not chat_id:
            return True, True, None, None, message
        
        # 检查插件是否启用
        if not is_plugin_enabled(chat_id):
            return True, True, None, None, message
        
        # 获取怒气prompt
        rage_prompt = rage_manager.get_rage_prompt(chat_id)
        if not rage_prompt:
            return True, True, None, None, message
        
        # 获取当前怒气状态
        state = rage_manager.get_rage(chat_id)
        
        # 注入prompt
        if message.llm_prompt:
            rage_header = f"\n【当前情绪状态 - 怒气等级{state.level}，怒气值{state.value:.0f}/100】\n{rage_prompt}\n"
            message.llm_prompt = rage_header + str(message.llm_prompt)
            message._modify_flags.modify_llm_prompt = True
            
            logger.info(f"[Rage] 注入怒气prompt，等级{state.level}，怒气值{state.value:.0f}")
        
        # 返回修改后的message对象
        return True, True, None, None, message


# ===== EventHandler - 启动初始化 =====

class RageStartupHandler(BaseEventHandler):
    """启动时初始化怒气系统并启动衰减循环

    说明：当前MaiBot插件系统不会自动调用插件类中的 on_load()，
    因此需要通过 ON_START 事件完成初始化，否则会出现：
    - 怒气 prompt 永远为空（未注入配置）
    - 自然衰减任务未启动（怒气值不会下降）
    """

    event_type = EventType.ON_START
    handler_name = "rage_startup"
    handler_description = "启动时初始化怒气配置并启动自然衰减"

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, str | None, None, Optional[MaiMessages]]:
        try:
            rage_manager.set_config(self.plugin_config or {})
            await rage_manager.start_decay_loop()
            logger.info("[Rage] 启动初始化完成")
            return True, True, None, None, message
        except Exception as e:
            logger.error(f"[Rage] 启动初始化失败: {e}", exc_info=True)
            return False, True, str(e), None, message


# ===== Command组件 =====

class ShowRageCommand(BaseCommand):
    """显示当前怒气值状态"""
    
    command_name = "rage_show"
    command_description = "显示当前怒气值状态：/rage show 或 /rage s"
    command_pattern = r"^/rage\s+(?:show|s)$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        try:
            chat_id = self.message.chat_stream.stream_id if self.message.chat_stream else None
            if not chat_id:
                return False, "无法获取聊天流信息", False
            
            state = rage_manager.get_rage(chat_id)
            
            level_desc = {
                0: "😊 心平气和",
                1: "😤 轻微不爽",
                2: "😠 明显生气",
                3: "🤬 暴怒中"
            }
            
            bar_length = 20
            filled = int(state.value / 100 * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            
            status_msg = f"""🔥 麦麦哈气状态 🔥

怒气值: {state.value:.1f}/100
[{bar}]

当前状态: {level_desc.get(state.level, "未知")}
怒气等级: Lv.{state.level}

命令:
• /rage show - 查看状态
• /rage set <值> - 设置怒气值
• /rage reset - 重置"""

            await send_api.text_to_stream(status_msg, chat_id, storage_message=False)
            return True, None, False
            
        except Exception as e:
            await self.send_text(f"获取状态失败: {e}", storage_message=False)
            return False, str(e), False


class SetRageCommand(BaseCommand):
    """设置怒气值"""
    
    command_name = "rage_set"
    command_description = "设置怒气值：/rage set <数值>"
    command_pattern = r"^/rage\s+set\s+(?P<value>[+-]?\d*\.?\d+)$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        try:
            if not self.matched_groups or "value" not in self.matched_groups:
                return False, "格式: /rage set <数值>", False
            
            value = float(self.matched_groups["value"])
            chat_id = self.message.chat_stream.stream_id if self.message.chat_stream else None
            
            if not chat_id:
                return False, "无法获取聊天流信息", False
            
            state = rage_manager.set_rage(chat_id, value)
            
            level_desc = {0: "😊", 1: "😤", 2: "😠", 3: "🤬"}
            
            await send_api.text_to_stream(
                f"🔥 怒气值: {state.value:.1f} {level_desc.get(state.level, '')}",
                chat_id, storage_message=False
            )
            return True, None, False
            
        except ValueError:
            await self.send_text("请输入有效数字", storage_message=False)
            return False, "数值格式错误", False
        except Exception as e:
            await self.send_text(f"失败: {e}", storage_message=False)
            return False, str(e), False


class ResetRageCommand(BaseCommand):
    """重置怒气值"""
    
    command_name = "rage_reset"
    command_description = "重置怒气值：/rage reset"
    command_pattern = r"^/rage\s+(?:reset|r)$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        try:
            chat_id = self.message.chat_stream.stream_id if self.message.chat_stream else None
            if not chat_id:
                return False, "无法获取聊天流信息", False
            
            rage_manager.reset_rage(chat_id)
            
            await send_api.text_to_stream(
                "😊 怒气值已重置~", chat_id, storage_message=False
            )
            return True, None, False
            
        except Exception as e:
            await self.send_text(f"失败: {e}", storage_message=False)
            return False, str(e), False


class ToggleRageCommand(BaseCommand):
    """开关插件（需要管理员权限）"""
    
    command_name = "rage_toggle"
    command_description = "开关怒气插件：/rage on|off [all]"
    command_pattern = r"^/rage\s+(?P<action>on|off)(?:\s+(?P<scope>all))?$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        try:
            chat_id = self.message.chat_stream.stream_id if self.message.chat_stream else None
            if not chat_id:
                return False, "无法获取聊天流信息", False
            
            # 鉴权检查
            user_id = str(self.message.user_info.user_id) if self.message.user_info else None
            if not user_id:
                return False, "无法获取用户信息", False
            
            if not check_admin_permission(user_id, rage_manager._config):
                await send_api.text_to_stream(
                    "⛔ 无权限，仅管理员可操作", chat_id, storage_message=False
                )
                return False, "无权限", False
            
            action = self.matched_groups.get("action", "on") if self.matched_groups else "on"
            scope = self.matched_groups.get("scope") if self.matched_groups else None
            enabled = action == "on"
            
            if scope == "all":
                # 全局开关
                set_global_enabled(enabled)
                status = "✅ 全局已开启" if enabled else "❌ 全局已关闭"
            else:
                # 当前群聊开关
                set_plugin_enabled(chat_id, enabled)
                status = "✅ 本群已开启" if enabled else "❌ 本群已关闭"
            
            await send_api.text_to_stream(
                f"🔥 麦麦哈气插件 {status}", chat_id, storage_message=False
            )
            return True, None, False
            
        except Exception as e:
            await self.send_text(f"失败: {e}", storage_message=False)
            return False, str(e), False


# ===== 插件注册 =====

@register_plugin
class MaiBotRagePlugin(BasePlugin):
    """麦麦哈气插件 - 怒气值系统"""
    
    plugin_name: str = "maibot_rage_plugin"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"
    
    config_section_descriptions = {
        "plugin": "插件基本信息",
        "rage": "怒气值系统配置",
        "prompts": "各等级怒气prompt",
        "features": "功能开关",
        "auth": "权限配置"
    }
    
    config_schema: dict = {
        "plugin": {
            "name": ConfigField(type=str, default="maibot_rage_plugin", description="插件名称"),
            "version": ConfigField(type=str, default="1.0.0", description="插件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "rage": {
            "max_rage": ConfigField(type=float, default=100.0, description="最大怒气值"),
            "decay_rate": ConfigField(type=float, default=4.0, description="每分钟衰减值"),
            "decay_interval": ConfigField(type=int, default=60, description="衰减间隔(秒)"),
            "provocation_mild": ConfigField(type=float, default=8.0, description="轻度挑衅增加怒气"),
            "provocation_moderate": ConfigField(type=float, default=18.0, description="中度挑衅增加怒气"),
            "provocation_severe": ConfigField(type=float, default=35.0, description="重度挑衅增加怒气"),
            "tease_amount": ConfigField(type=float, default=5.0, description="调戏增加怒气"),
            "annoy_amount": ConfigField(type=float, default=10.0, description="烦人增加怒气"),
        },
        "features": {
            "enable_commands": ConfigField(type=bool, default=True, description="启用命令"),
            "enable_decay": ConfigField(type=bool, default=True, description="启用自然衰减"),
        },
        "auth": {
            "admin_qq": ConfigField(type=list, default=[], description="管理员QQ号列表，为空则所有人可用"),
        },
    }

    async def on_load(self):
        """插件加载时初始化"""
        rage_manager.set_config(self.config)
        await rage_manager.start_decay_loop()
        logger.info("[RagePlugin] 麦麦哈气插件已加载")

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        components = [
            # Action - 由planner智能判断
            (ProvocationAction.get_action_info(), ProvocationAction),
            (TeaseAction.get_action_info(), TeaseAction),
            (AnnoyAction.get_action_info(), AnnoyAction),
            # EventHandler - 启动初始化（确保衰减循环启动、配置注入生效）
            (RageStartupHandler.get_handler_info(), RageStartupHandler),
            # EventHandler - prompt注入
            (RagePromptInjector.get_handler_info(), RagePromptInjector),
        ]
        
        if self.config.get("features", {}).get("enable_commands", True):
            components.extend([
                (ShowRageCommand.get_command_info(), ShowRageCommand),
                (SetRageCommand.get_command_info(), SetRageCommand),
                (ResetRageCommand.get_command_info(), ResetRageCommand),
                (ToggleRageCommand.get_command_info(), ToggleRageCommand),
            ])
        
        return components
