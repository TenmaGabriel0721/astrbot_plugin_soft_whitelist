from __future__ import annotations

import json
import os
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.star.filter.permission import PermissionType
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType


@register(
    "astrbot_plugin_soft_whitelist",
    "Gabriel",
    "只放行申请事件的高优先级软白名单插件",
    "v1.3.1",
    "local",
)
class SoftWhitelist(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.state_path = os.path.join(os.path.dirname(__file__), "auto_reply_state.json")
        self.auto_reply_state = self._load_auto_reply_state()
        logger.info("[soft_whitelist] 插件初始化完成")

    def _cfg(self, key: str, default=None):
        return self.config.get(key, default)

    def _set_cfg(self, key: str, value):
        self.config[key] = value
        self.config.save_config()

    def _list_cfg(self, key: str) -> list[str]:
        return [str(x) for x in self._cfg(key, [])]

    def _add_to_list_cfg(self, key: str, value: str) -> tuple[bool, list[str]]:
        data = self._list_cfg(key)
        if value in data:
            return False, data
        data.append(value)
        self._set_cfg(key, data)
        return True, data

    def _remove_from_list_cfg(self, key: str, value: str) -> tuple[bool, list[str]]:
        data = self._list_cfg(key)
        if value not in data:
            return False, data
        data.remove(value)
        self._set_cfg(key, data)
        return True, data

    def _load_auto_reply_state(self) -> dict:
        try:
            if not os.path.exists(self.state_path):
                return {}
            with open(self.state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"[soft_whitelist] 读取自动回复状态失败: {e}")
            return {}

    def _save_auto_reply_state(self):
        try:
            with open(self.state_path, 'w', encoding='utf-8') as f:
                json.dump(self.auto_reply_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[soft_whitelist] 保存自动回复状态失败: {e}")

    def _today_str(self) -> str:
        return datetime.now().strftime('%Y-%m-%d')

    def _auto_reply_key(self, scene: str, sender_id: str) -> str:
        return f"{scene}:{sender_id}"

    def _can_send_auto_reply(self, scene: str, sender_id: str) -> bool:
        if not sender_id:
            return False
        key = self._auto_reply_key(scene, sender_id)
        today = self._today_str()
        last_day = str(self.auto_reply_state.get(key, ''))
        return last_day != today

    def _mark_auto_reply_sent(self, scene: str, sender_id: str):
        if not sender_id:
            return
        key = self._auto_reply_key(scene, sender_id)
        self.auto_reply_state[key] = self._today_str()
        self._save_auto_reply_state()

    def _sender_id(self, event: AiocqhttpMessageEvent, raw_message: dict) -> str:
        sender_id = raw_message.get("user_id") or event.get_sender_id() or ""
        return str(sender_id) if sender_id is not None else ""

    def _group_id(self, event: AiocqhttpMessageEvent, raw_message: dict) -> str:
        group_id = raw_message.get("group_id") or event.get_group_id() or ""
        return str(group_id) if group_id is not None else ""

    def _is_group_admin(self, event: AiocqhttpMessageEvent) -> bool:
        try:
            sender = event.get_sender() or {}
            role = str(sender.get("role", ""))
            return role in {"owner", "admin"}
        except Exception:
            return False

    def _is_bot_admin(self, sender_id: str) -> bool:
        if not bool(self._cfg("allow_bot_admins", True)):
            return False
        try:
            admins = self.context.get_config().get("admins_id", []) or []
            admins = [str(x) for x in admins]
            return sender_id in admins
        except Exception:
            return False

    def _get_scene(self, raw_message: dict) -> str | None:
        if raw_message.get("post_type") == "notice":
            return "group" if raw_message.get("group_id") else "friend"
        if raw_message.get("post_type") != "message":
            return None

        message_type = raw_message.get("message_type")
        if message_type == "group":
            return "group"
        if message_type == "private":
            sub_type = str(raw_message.get("sub_type", ""))
            if sub_type == "friend":
                return "friend"
            return "temp"
        return None

    def _match_whitelist(self, scene: str, sender_id: str, group_id: str) -> bool:
        if scene == "group":
            user_whitelist = self._list_cfg("group_user_whitelist")
            group_whitelist = self._list_cfg("group_whitelist")
            return (sender_id in user_whitelist) or (group_id in group_whitelist)

        if scene == "friend":
            user_whitelist = self._list_cfg("friend_whitelist")
            return sender_id in user_whitelist

        if scene == "temp":
            user_whitelist = self._list_cfg("temp_user_whitelist")
            group_whitelist = self._list_cfg("temp_group_whitelist")
            return (sender_id in user_whitelist) or (group_id in group_whitelist)

        return False

    def _scene_enabled(self, scene: str) -> bool:
        return bool(self._cfg(f"{scene}_enabled", True))

    def _allow_message_scene(self, event: AiocqhttpMessageEvent, raw_message: dict, scene: str) -> bool:
        if not bool(self._cfg("enabled", True)):
            return True

        sender_id = self._sender_id(event, raw_message)
        self_id = str(event.get_self_id()) if event.get_self_id() is not None else ""
        group_id = self._group_id(event, raw_message)

        if not self._scene_enabled(scene):
            return True

        if bool(self._cfg("allow_self", True)) and sender_id and sender_id == self_id:
            return True

        if self._is_bot_admin(sender_id):
            return True

        if scene == "group" and bool(self._cfg("allow_group_admins", True)) and self._is_group_admin(event):
            return True

        return self._match_whitelist(scene, sender_id, group_id)

    def _is_allowed_request(self, raw_message: dict) -> bool:
        if raw_message.get("post_type") != "request":
            return False

        request_type = str(raw_message.get("request_type", ""))
        if request_type == "friend":
            return True
        if request_type == "group" and str(raw_message.get("sub_type", "")) == "invite":
            return True
        return False

    def _get_block_reply(self, scene: str) -> str:
        if scene == "friend":
            return str(self._cfg("friend_block_reply_text", "你暂时不在我的白名单里，先别急着找我哦"))
        if scene == "temp":
            return str(self._cfg("temp_block_reply_text", "你暂时不在我的白名单里，可以先走审批哦"))
        return ""

    def _should_auto_reply_when_blocked(self, scene: str, raw_message: dict, sender_id: str) -> bool:
        if scene == "friend":
            if not bool(self._cfg("friend_block_auto_reply", False)):
                return False
        elif scene == "temp":
            if not bool(self._cfg("temp_block_auto_reply", False)):
                return False
        else:
            return False

        # 仅在收到对方发来的消息时回复
        if raw_message.get("post_type") != "message":
            return False
        if not sender_id:
            return False

        # 每人每天最多回复一次
        return self._can_send_auto_reply(scene, sender_id)

    def _format_status(self) -> str:
        lines = [
            "【软白名单状态】",
            f"总开关: {'开' if self._cfg('enabled', True) else '关'}",
            f"放行Bot自身: {'开' if self._cfg('allow_self', True) else '关'}",
            f"放行AstrBot管理员: {'开' if self._cfg('allow_bot_admins', True) else '关'}",
            f"放行群管理: {'开' if self._cfg('allow_group_admins', True) else '关'}",
            "",
            "事件策略:",
            "- 仅放行 request.friend 和 request.group.invite",
            "- 其余 request / notice / meta_event 默认拦截",
            "- message 按白名单放行，其余拦截",
            "- 非白名单自动回复：仅对方来消息时触发，且每人每天最多一次",
            "",
            f"群聊白名单: {'开' if self._cfg('group_enabled', True) else '关'}",
            f"- 群白名单: {', '.join(self._list_cfg('group_whitelist')) or '空'}",
            f"- 群成员白名单: {', '.join(self._list_cfg('group_user_whitelist')) or '空'}",
            "",
            f"好友白名单: {'开' if self._cfg('friend_enabled', True) else '关'}",
            f"- 好友白名单: {', '.join(self._list_cfg('friend_whitelist')) or '空'}",
            f"- 非白名单自动回复: {'开' if self._cfg('friend_block_auto_reply', False) else '关'}",
            f"- 自动回复内容: {self._cfg('friend_block_reply_text', '') or '空'}",
            "",
            f"临时会话白名单: {'开' if self._cfg('temp_enabled', True) else '关'}",
            f"- 临时用户白名单: {', '.join(self._list_cfg('temp_user_whitelist')) or '空'}",
            f"- 临时来源群白名单: {', '.join(self._list_cfg('temp_group_whitelist')) or '空'}",
            f"- 非白名单自动回复: {'开' if self._cfg('temp_block_auto_reply', False) else '关'}",
            f"- 自动回复内容: {self._cfg('temp_block_reply_text', '') or '空'}",
        ]
        return "\n".join(lines)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("软白名单状态", alias={"白名单状态"})
    async def white_status(self, event: AiocqhttpMessageEvent):
        yield event.plain_result(self._format_status())

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("加群白")
    async def add_group_white(self, event: AiocqhttpMessageEvent, target: str = ""):
        target = str(target).strip()
        if not target:
            yield event.plain_result("用法: ~加群白 群号")
            return
        ok, data = self._add_to_list_cfg("group_whitelist", target)
        if ok:
            yield event.plain_result(f"已加入群白名单: {target}")
        else:
            yield event.plain_result(f"这个群已经在白名单里了: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删群白")
    async def del_group_white(self, event: AiocqhttpMessageEvent, target: str = ""):
        target = str(target).strip()
        if not target:
            yield event.plain_result("用法: ~删群白 群号")
            return
        ok, data = self._remove_from_list_cfg("group_whitelist", target)
        if ok:
            yield event.plain_result(f"已移出群白名单: {target}")
        else:
            yield event.plain_result(f"这个群不在白名单里: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("加群员白")
    async def add_group_user_white(self, event: AiocqhttpMessageEvent, target: str = ""):
        target = str(target).strip()
        if not target:
            yield event.plain_result("用法: ~加群员白 QQ号")
            return
        ok, data = self._add_to_list_cfg("group_user_whitelist", target)
        if ok:
            yield event.plain_result(f"已加入群成员白名单: {target}")
        else:
            yield event.plain_result(f"这个QQ已经在群成员白名单里了: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删群员白")
    async def del_group_user_white(self, event: AiocqhttpMessageEvent, target: str = ""):
        target = str(target).strip()
        if not target:
            yield event.plain_result("用法: ~删群员白 QQ号")
            return
        ok, data = self._remove_from_list_cfg("group_user_whitelist", target)
        if ok:
            yield event.plain_result(f"已移出群成员白名单: {target}")
        else:
            yield event.plain_result(f"这个QQ不在群成员白名单里: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("加好友白")
    async def add_friend_white(self, event: AiocqhttpMessageEvent, target: str = ""):
        target = str(target).strip()
        if not target:
            yield event.plain_result("用法: ~加好友白 QQ号")
            return
        ok, data = self._add_to_list_cfg("friend_whitelist", target)
        if ok:
            yield event.plain_result(f"已加入好友白名单: {target}")
        else:
            yield event.plain_result(f"这个QQ已经在好友白名单里了: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删好友白")
    async def del_friend_white(self, event: AiocqhttpMessageEvent, target: str = ""):
        target = str(target).strip()
        if not target:
            yield event.plain_result("用法: ~删好友白 QQ号")
            return
        ok, data = self._remove_from_list_cfg("friend_whitelist", target)
        if ok:
            yield event.plain_result(f"已移出好友白名单: {target}")
        else:
            yield event.plain_result(f"这个QQ不在好友白名单里: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("加临时白")
    async def add_temp_white(self, event: AiocqhttpMessageEvent, kind: str = "", target: str = ""):
        kind = str(kind).strip()
        target = str(target).strip()
        if kind not in {"用户", "群"} or not target:
            yield event.plain_result("用法: ~加临时白 用户 QQ号  或  ~加临时白 群 群号")
            return
        key = "temp_user_whitelist" if kind == "用户" else "temp_group_whitelist"
        ok, data = self._add_to_list_cfg(key, target)
        if ok:
            yield event.plain_result(f"已加入临时{kind}白名单: {target}")
        else:
            yield event.plain_result(f"这个目标已经在临时{kind}白名单里了: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删临时白")
    async def del_temp_white(self, event: AiocqhttpMessageEvent, kind: str = "", target: str = ""):
        kind = str(kind).strip()
        target = str(target).strip()
        if kind not in {"用户", "群"} or not target:
            yield event.plain_result("用法: ~删临时白 用户 QQ号  或  ~删临时白 群 群号")
            return
        key = "temp_user_whitelist" if kind == "用户" else "temp_group_whitelist"
        ok, data = self._remove_from_list_cfg(key, target)
        if ok:
            yield event.plain_result(f"已移出临时{kind}白名单: {target}")
        else:
            yield event.plain_result(f"这个目标不在临时{kind}白名单里: {target}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("好友白回复")
    async def set_friend_reply(self, event: AiocqhttpMessageEvent, mode: str = "", text: str = ""):
        mode = str(mode).strip()
        text = str(text).strip()
        if mode in {"开", "关"}:
            self._set_cfg("friend_block_auto_reply", mode == "开")
            yield event.plain_result(f"好友非白名单自动回复已{'开启' if mode == '开' else '关闭'}")
            return
        if mode == "设定":
            if not text:
                yield event.plain_result("用法: ~好友白回复 设定 回复内容")
                return
            self._set_cfg("friend_block_reply_text", text)
            yield event.plain_result(f"好友非白名单自动回复内容已更新:\n{text}")
            return
        yield event.plain_result("用法: ~好友白回复 开|关  或  ~好友白回复 设定 回复内容")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("临时白回复")
    async def set_temp_reply(self, event: AiocqhttpMessageEvent, mode: str = "", text: str = ""):
        mode = str(mode).strip()
        text = str(text).strip()
        if mode in {"开", "关"}:
            self._set_cfg("temp_block_auto_reply", mode == "开")
            yield event.plain_result(f"临时会话非白名单自动回复已{'开启' if mode == '开' else '关闭'}")
            return
        if mode == "设定":
            if not text:
                yield event.plain_result("用法: ~临时白回复 设定 回复内容")
                return
            self._set_cfg("temp_block_reply_text", text)
            yield event.plain_result(f"临时会话非白名单自动回复内容已更新:\n{text}")
            return
        yield event.plain_result("用法: ~临时白回复 开|关  或  ~临时白回复 设定 回复内容")

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100000)
    async def soft_filter(self, event: AiocqhttpMessageEvent):
        try:
            raw_message = getattr(event.message_obj, "raw_message", None)
            if not isinstance(raw_message, dict):
                return

            post_type = str(raw_message.get("post_type", ""))

            if post_type == "request":
                if self._is_allowed_request(raw_message):
                    return
                logger.info(f"[soft_whitelist] 已拦截 request: {raw_message}")
                event.stop_event()
                return
            

            if post_type not in ["message", "notice"]:
                logger.info(f"[soft_whitelist] 已拦截非消息事件 post_type={post_type}")
                event.stop_event()
                return

            scene = self._get_scene(raw_message)
            if scene is None:
                logger.info(f"[soft_whitelist] 无法识别消息场景，已拦截: {raw_message}")
                event.stop_event()
                return

            if self._allow_message_scene(event, raw_message, scene):
                return

            sender_id = self._sender_id(event, raw_message)
            group_id = self._group_id(event, raw_message)
            logger.info(
                f"[soft_whitelist] 已拦截消息 scene={scene}, sender={sender_id}, group={group_id}"
            )

            if self._should_auto_reply_when_blocked(scene, raw_message, sender_id):
                reply_text = self._get_block_reply(scene).strip()
                if reply_text:
                    yield event.plain_result(reply_text)
                    self._mark_auto_reply_sent(scene, sender_id)

            event.stop_event()
        except Exception as e:
            logger.error(f"[soft_whitelist] 拦截异常: {e}", exc_info=True)
