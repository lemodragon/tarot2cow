import os
import json
import random
import requests
import re
from datetime import datetime, timedelta
import pytz

from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *
from common.expired_dict import ExpiredDict

@register(
    name="Tarot2cow",
    desc="A plugin for tarot divination with multiple themes and configurable daily limits.",
    version="6.7",
    author="lemodragon",
    desire_priority=90
)
class Tarot2cow(Plugin):
    def __init__(self):
        super().__init__()
        try:
            conf = self.load_config()
            if not conf:
                raise Exception("配置未找到。")

            self.chain_reply = conf.get("chain_reply", True)
            self.tarot_json_path = os.path.join(os.path.dirname(__file__), "tarot.json")
            
            self.divine_prefixes = conf.get("divine_prefixes", ["%占卜", "？占卜"])
            self.tarot_prefixes = conf.get("tarot_prefixes", ["%塔罗牌", "？塔罗牌"])
            self.interpret_prefix = "%解读"
            
            self.enable_daily_limit = conf.get("enable_daily_limit", True)
            self.daily_divine_limit = conf.get("daily_divine_limit", 1)
            self.daily_tarot_limit = conf.get("daily_tarot_limit", 1)

            self.timezone = pytz.timezone('Asia/Shanghai')

            self.load_tarot_data()

            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

            self.last_draw = ExpiredDict(3600)
            
            self.user_draw_counts = {}

            logger.info(f"[Tarot2cow] 初始化成功")
            logger.info(f"[Tarot2cow] 占卜前缀: {self.divine_prefixes}")
            logger.info(f"[Tarot2cow] 塔罗牌前缀: {self.tarot_prefixes}")
            logger.info(f"[Tarot2cow] 每日抽牌限制: {'启用' if self.enable_daily_limit else '禁用'}")
            logger.info(f"[Tarot2cow] 每日占卜次数限制: {self.daily_divine_limit}")
            logger.info(f"[Tarot2cow] 每日塔罗牌次数限制: {self.daily_tarot_limit}")
        except Exception as e:
            logger.error(f"[Tarot2cow] 初始化失败，错误：{e}")
            raise e

    def load_tarot_data(self):
        if not os.path.exists(self.tarot_json_path) or self.is_update_needed():
            self.update_tarot_data()
        with open(self.tarot_json_path, 'r', encoding='utf-8') as f:
            self.tarot_data = json.load(f)
        logger.info(f"[Tarot2cow] 加载了 {len(self.tarot_data['cards'])} 张塔罗牌")

    def is_update_needed(self):
        if not os.path.exists(self.tarot_json_path):
            return True
        last_modified = datetime.fromtimestamp(os.path.getmtime(self.tarot_json_path))
        return datetime.now() - last_modified > timedelta(days=7)

    def update_tarot_data(self):
        url = "https://raw.githubusercontent.com/lemodragon/tarot2cow/main/tarot.json"
        response = requests.get(url)
        if response.status_code == 200:
            with open(self.tarot_json_path, 'w', encoding='utf-8') as f:
                f.write(response.text)
            logger.info("[Tarot2cow] 塔罗牌数据更新成功")
        else:
            logger.error("[Tarot2cow] 塔罗牌数据更新失败")

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type != ContextType.TEXT:
            return

        content = e_context["context"].content.strip()
        logger.debug(f"[Tarot2cow] 收到消息: {content}")

        try:
            if content.startswith(tuple(self.divine_prefixes)):
                logger.info("[Tarot2cow] 触发占卜功能")
                self.divine(e_context)
                return
            elif content.startswith(tuple(self.tarot_prefixes)):
                logger.info("[Tarot2cow] 触发单张塔罗牌功能")
                self.draw_single_card(e_context)
                return
            elif content.startswith(self.interpret_prefix):
                logger.info("[Tarot2cow] 触发解读功能")
                self.interpret(e_context)
                return
        except Exception as e:
            logger.error(f"[Tarot2cow] 处理消息时发生错误: {e}")
            e_context["reply"] = Reply(ReplyType.ERROR, content=f"发生错误: {str(e)}")
            e_context.action = EventAction.BREAK_PASS

    def can_draw(self, user_id, draw_type):
        if not self.enable_daily_limit:
            return True, ""
        
        now = datetime.now(self.timezone)
        today = now.date()
        
        if user_id not in self.user_draw_counts or self.user_draw_counts[user_id]["date"] != today:
            self.user_draw_counts[user_id] = {"date": today, "divine": 0, "tarot": 0}
        
        user_counts = self.user_draw_counts[user_id]
        
        if draw_type == "divine" and user_counts["divine"] >= self.daily_divine_limit:
            next_draw = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(tzinfo=self.timezone)
            time_left = next_draw - now
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            return False, f"今天的占卜次数已用完啦！😊 请在 {hours} 小时 {minutes} 分钟后再来吧！"
        
        if draw_type == "tarot" and user_counts["tarot"] >= self.daily_tarot_limit:
            next_draw = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(tzinfo=self.timezone)
            time_left = next_draw - now
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            return False, f"今天的塔罗牌次数已用完啦！😊 请在 {hours} 小时 {minutes} 分钟后再来吧！"
        
        user_counts[draw_type] += 1
        return True, ""

    def divine(self, e_context):
        user_id = e_context["context"]["session_id"]
        can_draw, message = self.can_draw(user_id, "divine")
        if not can_draw:
            e_context["reply"] = Reply(ReplyType.TEXT, content=message)
            e_context.action = EventAction.BREAK_PASS
            return

        formation = random.choice(list(self.tarot_data["formations"].keys()))
        formation_data = self.tarot_data["formations"][formation]
        cards_num = formation_data["cards_num"]
        representations = random.choice(formation_data["representations"])

        cards = random.sample(list(self.tarot_data["cards"].values()), cards_num)

        result = f"✨ 启用{formation}牌阵，抽取了{cards_num}张牌：\n\n"
        image_urls = []

        for i, (card, representation) in enumerate(zip(cards, representations)):
            orientation = random.choice(["正位", "逆位"])
            meaning = card["meaning"]["up"] if orientation == "正位" else card["meaning"]["down"]
            card_result = f"{i+1}. {representation}：{card['name_cn']}（{orientation}）\n   含义：{meaning}\n"
            if 'image_url' in card:
                image_url = self.extract_image_url(card['image_url'])
                if image_url:
                    image_urls.append(image_url)
            result += card_result + "\n"

        self.last_draw[user_id] = self.remove_image_urls(result)

        if image_urls:
            e_context["reply"] = Reply(ReplyType.IMAGE_URL, content=image_urls[0])
        else:
            e_context["reply"] = Reply(ReplyType.TEXT, content="抱歉，无法获取塔罗牌图片。")

        e_context.action = EventAction.BREAK_PASS
        return e_context

    def draw_single_card(self, e_context):
        user_id = e_context["context"]["session_id"]
        can_draw, message = self.can_draw(user_id, "tarot")
        if not can_draw:
            e_context["reply"] = Reply(ReplyType.TEXT, content=message)
            e_context.action = EventAction.BREAK_PASS
            return

        card = random.choice(list(self.tarot_data["cards"].values()))
        orientation = random.choice(["正位", "逆位"])
        meaning = card["meaning"]["up"] if orientation == "正位" else card["meaning"]["down"]

        result = f"🃏 抽到了 {card['name_cn']}（{orientation}）\n含义：{meaning}\n"

        self.last_draw[user_id] = result

        if 'image_url' in card:
            image_url = self.extract_image_url(card['image_url'])
            if image_url:
                e_context["reply"] = Reply(ReplyType.IMAGE_URL, content=image_url)
            else:
                e_context["reply"] = Reply(ReplyType.TEXT, content="抱歉，无法获取塔罗牌图片。")
        else:
            e_context["reply"] = Reply(ReplyType.TEXT, content="抱歉，无法获取塔罗牌图片。")

        e_context.action = EventAction.BREAK_PASS
        return e_context

    def interpret(self, e_context):
        session_id = e_context["context"]["session_id"]
        if session_id not in self.last_draw:
            e_context["reply"] = Reply(ReplyType.TEXT, content="抱歉，没有找到最近的抽牌结果。请先进行占卜或抽取单张塔罗牌。")
            e_context.action = EventAction.BREAK_PASS
            return

        last_draw = self.last_draw[session_id]
        
        prompt = f"请为以下塔罗牌结果进行详细解读：\n\n{last_draw}\n\n"
        prompt += "请给出整体的解读，并分析各个卡片之间的关系和对问题的指引。解读应该包括以下几个方面：\n"
        prompt += "1. 每张牌在当前位置的含义\n"
        prompt += "2. 牌与牌之间的关系和互动\n"
        prompt += "3. 整体牌阵所揭示的主题或问题\n"
        prompt += "4. 对未来的预测或建议\n"
        prompt += "请用通俗易懂的语言进行解读，避免使用过于专业或晦涩的术语。"
        prompt += "在解读中适当加入表情符号，使文本更加生动有趣。保持文本清晰易读，不要使用任何特殊格式或标记。"
        prompt += "在关键点或重要概念前可以使用emoji表情，如🔮、💫、🌟等，增加视觉吸引力。"
        prompt += "总结部分可以用'💡总结：'开头，使其更加醒目。"
        prompt += "请注意，不要使用任何Markdown语法或其他特殊格式，只需使用纯文本和emoji。"

        e_context["context"].content = prompt
        e_context.action = EventAction.CONTINUE
        return e_context

    def extract_image_url(self, text: str) -> str:
        match = re.search(r'(https?://[^\s]+?\.(?:png|jpe?g|gif|bmp|webp|svg|tiff|ico))(?:\s|$)', text, re.IGNORECASE)
        url = match.group(1) if match else None
        logger.debug(f"[Tarot2cow] 提取的图片URL: {url}")
        return url

    def remove_image_urls(self, text: str) -> str:
        cleaned_text = re.sub(r'https?://\S+\.(?:png|jpe?g|gif|bmp|webp|svg|tiff|ico)(?:\s|$)', '', text, flags=re.IGNORECASE)
        logger.debug(f"[Tarot2cow] 移除图片URL后的文本: {cleaned_text}")
        return cleaned_text

    def get_help_text(self, **kwargs):
        help_text = "🔮 塔罗牌占卜插件使用指南：\n\n"
        help_text += f"1. 输入 '{self.divine_prefixes[0]}' 进行完整的塔罗牌占卜\n"
        help_text += f"2. 输入 '{self.tarot_prefixes[0]}' 抽取单张塔罗牌\n"
        help_text += f"3. 输入 '{self.interpret_prefix}' 获取最近一次抽牌的详细解读\n\n"
        if self.enable_daily_limit:
            help_text += f"注意：每位用户每天可以进行 {self.daily_divine_limit} 次占卜和 {self.daily_tarot_limit} 次单张塔罗牌抽取。次日凌晨00:00后重置次数。"
        return help_text