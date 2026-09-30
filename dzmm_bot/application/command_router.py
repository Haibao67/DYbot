import logging
import json
from sqlalchemy import select
from dzmm_bot.persistence.schema import reply_previews
from dzmm_bot.presentation.compact import reply_pages
from dzmm_bot.presentation.command_hints import command_hint
from difflib import SequenceMatcher
from dzmm_bot.domain.economy import GameError, quantity
from dzmm_bot.presentation.messages import help_text, error, render
from dzmm_bot.presentation.ruihe import menu_text, render_ranch, render_inventory
from dzmm_bot.presentation.ruihe import render_factory, render_factory_recipes
from dzmm_bot.domain.factory_rules import RECIPE_NAMES, RECIPES
from dzmm_bot.domain.buff_rules import BUFF_NAMES
from .factory_service import FactoryService
from .horse_service import HorseService
from dzmm_bot.presentation.horses import render_horse
from .farm_service import FarmService
from dzmm_bot.presentation.farm import render_farm
from dzmm_bot.domain.farm_rules import CROP_NAMES, FEED_NAMES, ECONOMIC_CROPS
from dzmm_bot.domain.command_aliases import normalize_command_parts
from dzmm_bot.presentation.formatters import name_escape
from .services import GameService, uid
from .announcement_service import AnnouncementService, AnnouncementError
from .admin_login_service import AdminLoginService
from .invite_approval_service import InviteApprovalService
from .asset_service import AssetService, coin_amount
from .auction_service import AuctionService, price_cents
from .guidance_service import GuidanceService, DailyBriefService, TodoService
from .world_event_service import WorldEventService
from dzmm_bot.presentation.information import render_update

log = logging.getLogger(__name__)
ANIMALS = {"鸡": "chicken", "羊": "sheep", "牛": "cow", "饲料": "feed", "精饲料": "premium_feed"}
PRODUCTS = {"鸡蛋": "egg", "羊毛": "wool", "牛奶": "milk", "蛋糕": "cake", "毛衣": "sweater",
            "奶酪": "cheese", "礼盒": "gift_box", "羽绒服": "down_coat",
            "奶酪拼盘": "cheese_platter", "大礼包": "grand_gift", "全部": "all"}
PRODUCTS.update({name: code for name, code in RECIPE_NAMES.items() if code not in PRODUCTS.values()})
PRODUCTS.update({rule["name"]: code for code, rule in ECONOMIC_CROPS.items()})


class CommandRouter:
    def __init__(self, db, now, secret, stage="full", whitelist=None, admins=None,
                 world_event_policy=None, admin_login_password_hash="", auction_cooldown_seconds=300,
                 competition_options=None, weather_enabled=False, race_commentary_options=None):
        self.db, self.now, self.secret = db, now, secret
        self.stage, self.whitelist = stage, set(whitelist or [])
        self.admins = set(admins or [])
        self.admin_login_password_hash = admin_login_password_hash
        self.world_event_policy = dict(world_event_policy or {})
        self.auction_cooldown_seconds = auction_cooldown_seconds
        self.competition_options = dict(competition_options or {})
        self.weather_enabled = bool(weather_enabled)
        self.race_commentary_options = dict(race_commentary_options or {})

    def dispatch(self, event, room):
        parts = normalize_command_parts(event.text.strip().split())
        condition = (reply_previews.c.room_id == event.room, reply_previews.c.player_id == event.sender)
        if parts and parts[0] == "/回复页":
            if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdigit() or len(parts[1]) > 4:
                return "请使用 /回复页 页码。"
            row = self.db.execute(select(reply_previews).where(*condition)).mappings().first()
            pages = json.loads(row["pages_json"]) if row and self.now - row["created_at"] <= 86400 else []
            page = int(parts[1])
            if not 1 <= page <= len(pages):
                return "没有该页内容，请重新查询原来的面板。"
            return self._page_text(pages, page)
        rendered = self._dispatch(event, room)
        if rendered is None:
            return None
        automatic = rendered.startswith(('📈 铃露行情', '🏆 财富排行榜', '🏁 '))
        preserve_lines = automatic or rendered.startswith("—— 📖")
        command = normalize_command_parts(event.text.strip().split())
        command_name = command[0] if command else ""
        keep_current = self._keep_current_reply(command_name, rendered)
        pages = reply_pages(rendered, preserve_lines=preserve_lines, simplify=not keep_current)
        if automatic:
            return pages if len(pages) > 1 else pages[0]
        if len(pages) > 1:
            existing = self.db.execute(select(reply_previews.c.room_id).where(*condition)).first()
            values = {"pages_json": json.dumps(pages, ensure_ascii=False), "created_at": self.now}
            if existing:
                self.db.execute(reply_previews.update().where(*condition).values(**values))
            else:
                self.db.execute(reply_previews.insert().values(room_id=event.room, player_id=event.sender, **values))
            return self._page_text(pages, 1)
        return pages[0] if pages else "暂无内容。"

    @staticmethod
    def _keep_current_reply(command, rendered=""):
        """Commands whose approved catalog wording must remain untouched."""
        exact = {
            "/收取", "/收获", "/配方", "/加工配方",
            "/马厩", "/买马", "/马", "/马匹命名", "/喂马", "/繁育", "/接生",
            "/强制接生", "/退役", "/马册", "/血统", "/马匹详情", "/成马",
            "/赛程", "/比赛详情", "/报名", "/取消报名", "/观赛", "/赛果", "/战绩",
            "/开赛", "/强制开赛", "/更新", "/更新日志", "/怎么玩",
            "/预览更新", "/发布更新", "/创建更新", "/公告状态", "/公告失败",
            "/重试公告", "/拍卖", "/拍卖场", "/拍卖详情", "/拍卖记录",
            "/我的拍卖", "/我的出价", "/寄拍", "/拍", "/跟", "/下架拍卖",
            "/领取拍卖马",
        }
        if command in exact:
            return True
        panels = {
            "/牧场": ("🏡", "—— 🏡"),
            "/库存": ("📦 我的库存",),
            "/农场": ("🌱 我的农场",),
            "/农田详情": ("🌱 农田详情",),
            "/播种": ("🌱 已种下", "🌱 播种"),
            "/加工厂": ("🏭", "—— 🏭"),
        }
        if command in panels:
            return rendered.startswith(panels[command])
        # Scheduled checkpoints and race announcements have no originating command.
        return rendered.startswith(("🏇 ", "🏁 "))

    @staticmethod
    def _page_text(pages, page):
        commands = []
        if page > 1:
            commands.append(f"/回复页 {page - 1}")
        if page < len(pages):
            commands.append(f"/回复页 {page + 1}")
        return pages[page - 1] + f"\n第{page}/{len(pages)}页｜" + " | ".join(commands)

    def _dispatch(self, event, room):
        parts = normalize_command_parts(event.text.strip().split())
        if not parts or not parts[0].startswith("/"):
            return None
        original_command = parts[0]
        if parts[0] in {'/开赛','/强制开赛','/赛程','/比赛详情','/报名','/取消报名','/观赛','/赛果','/战绩'} or (len(parts) >= 2 and parts[:2] == ['/排行榜','赛马']):
            from .competition_service import CompetitionService, CompetitionError
            from .race_queries import RaceQueries
            from dzmm_bot.domain.horse_racing import RacingConfigurationError
            try:
                with self.db.begin_nested():
                    queries = RaceQueries(self.db,self.now)
                    if parts[0] in {'/开赛','/强制开赛'}:
                        if not AdminLoginService(self.db,self.now,self.admin_login_password_hash,self.admins).is_admin(event.sender):
                            raise GameError('admin_only')
                        if parts[0]=='/开赛' and len(parts) not in (4,5,6):
                            return '用法：/开赛 名称 距离米 报名分钟 [参赛上限] [草地|泥地]\n例：/开赛 春风杯 2000 10 20 草地\n距离大于1000且为100整数倍，上限8～20匹；默认20匹、草地'
                        if parts[0]=='/强制开赛':
                            if len(parts) not in (1,2):
                                return '用法：/强制开赛 赛事｜例：/强制开赛 澄露杯'
                            parts=['/强制开赛',parts[1] if len(parts)==2 else '澄露杯']
                    if len(parts)==1 and parts[0] in {'/比赛详情','/观赛','/赛果'}:
                        parts=[parts[0],queries.current_race_id()]
                    elif len(parts)==3 and parts[0]=='/报名':
                        parts=[parts[0],queries.current_race_id(registration=True),*parts[1:]]
                    if ((parts[0] in {'/比赛详情','/观赛','/赛果','/强制开赛'} and len(parts)==2)
                            or (parts[0]=='/报名' and len(parts)==4)
                            or (parts[0]=='/取消报名' and len(parts)==3)):
                        parts[1]=queries.row(parts[1],registration=parts[0]=='/报名')['race_id']
                    if parts == ['/赛程']:
                        return queries.schedule()
                    if parts[:2] == ['/排行榜','赛马'] and len(parts) in (2,3):
                        return queries.ranking(parts[2] if len(parts) == 3 else '奖金')
                    if len(parts) == 2 and parts[0] == '/比赛详情':
                        return queries.detail(parts[1])
                    if len(parts) == 2 and parts[0] in {'/观赛','/赛果'}:
                        return queries.result(parts[1],watch=parts[0] == '/观赛')
                    if len(parts) == 2 and parts[0] == '/战绩':
                        horse = HorseService(self.db,self.now,self.secret,event.sender,event.room).resolve_horse(parts[1],True)
                        return queries.record(horse)
                    options=self.competition_options
                    if not options:
                        from dzmm_bot.domain.race_runtime import RaceRuntimeFactory
                        runtime=RaceRuntimeFactory.build()
                        options={'engine':runtime.engine,'npc_factory':runtime.npc_factory}
                    service = CompetitionService(self.db,self.now,self.secret,event.sender,event.room,event.message_id,
                        race_commentary_options=self.race_commentary_options, **options)
                    if parts[0]=='/强制开赛':
                        service.admin_force_start(parts[1],self.admins,self.admin_login_password_hash)
                        return '✅ 赛事已开跑并完成结算\n'+queries.result(parts[1])
                    if parts[0]=='/开赛':
                        numeric=parts[2:4]+([parts[4]] if len(parts)>=5 else [])
                        if any(not v.isascii() or not v.isdigit() or len(v)>6 for v in numeric):
                            raise CompetitionError('距离、报名分钟和参赛上限须为正整数。｜/开赛 春风杯 2000 10 20 草地')
                        surface={'草地':'turf','泥地':'dirt'}.get(parts[5] if len(parts)==6 else '草地','')
                        race_id=service.admin_create(parts[1],int(parts[2]),int(parts[3]),surface,
                            int(parts[4]) if len(parts)>=5 else 20,self.admins,self.admin_login_password_hash)
                        return '✅ 自定义赛事已创建并开放报名\n'+queries.detail(race_id)
                    if len(parts) == 4 and parts[0] == '/报名':
                        style = {'逃':'front','先':'stalker','先行':'stalker','差':'mid','追':'closer'}.get(parts[3],parts[3])
                        return service.register(parts[1],parts[2],style)
                    if len(parts) == 3 and parts[0] == '/取消报名':
                        return service.cancel_entry(parts[1],parts[2])
                    return '🏇 用法：/赛程 | /比赛详情 赛事 | /报名 赛事 马名 逃 | /取消报名 赛事 马名 | /观赛 赛事 | /赛果 赛事 | /战绩 马名'
            except CompetitionError as exc:
                return str(exc)
            except RacingConfigurationError:
                return '赛马规则配置待确认，相关功能暂未开放。｜/赛程'
            except GameError as exc:
                return error(exc.code,**exc.details)
        if parts[0] == '/强制接生':
            try:
                with self.db.begin_nested():
                    if not AdminLoginService(self.db,self.now,self.admin_login_password_hash,self.admins).is_admin(event.sender):
                        raise GameError('admin_only')
                    if len(parts)!=1:
                        return '用法：/强制接生｜立即接生自己所有待产幼驹｜查看：/马厩'
                    result=HorseService(self.db,self.now,self.secret,event.sender,event.room,event.message_id).admin_deliver_foals(
                        self.admins,self.admin_login_password_hash)
                    return render_horse(result)
            except GameError as exc:
                return error(exc.code,**exc.details)
        if parts[0] == '/成马':
            try:
                with self.db.begin_nested():
                    if not AdminLoginService(self.db,self.now,self.admin_login_password_hash,self.admins).is_admin(event.sender):
                        raise GameError('admin_only')
                    if len(parts)!=2:
                        return '用法：/成马 <马名>｜例：/成马 春风｜查看自己的马：/马厩'
                    return HorseService(self.db,self.now,self.secret,event.sender,event.room,event.message_id).admin_mature(
                        parts[1],self.admins,self.admin_login_password_hash)
            except GameError as exc:
                return error(exc.code,**exc.details)
        if parts[0] == "/管理员登陆":
            with self.db.begin_nested():
                return AdminLoginService(self.db, self.now, self.admin_login_password_hash,
                                         self.admins).login(event.sender, room["kind"], parts)
        if parts[0] == "/结束红包":
            try:
                with self.db.begin_nested():
                    if not AdminLoginService(self.db,self.now,self.admin_login_password_hash,
                                             self.admins).is_admin(event.sender):
                        raise GameError("admin_only")
                    if room["kind"] != "group":
                        raise GameError("red_packet_group")
                    if len(parts) != 1:
                        raise GameError("red_packet_syntax")
                    from .red_packet_service import RedPacketService
                    packet=RedPacketService(self.db,self.now,self.secret,event.sender,event.room,event.message_id)
                    return render(packet.end(), event.message_id, self.stage)
            except GameError as exc:
                return error(exc.code,**exc.details)
        if parts[0] == "/公告":
            try:
                with self.db.begin_nested():
                    if not AdminLoginService(self.db, self.now, self.admin_login_password_hash,
                                             self.admins).is_admin(event.sender):
                        return "⚠️ 该指令仅限管理员使用。"
                    if not room or room["kind"] != "private":
                        return "⚠️ 请私聊 Bot 使用：/公告 <内容>"
                    command_parts = event.text.strip().split(maxsplit=1)
                    if len(command_parts) != 2 or not command_parts[1].strip():
                        return "公告格式：/公告 公告正文"
                    return AnnouncementService(self.db, self.now, event.sender).announce(command_parts[1])
            except AnnouncementError as exc:
                return str(exc)
        info_commands = {"/更新", "/更新日志", "/怎么玩", "/今日", "/天气", "/待办", "/创建更新",
                         "/预览更新", "/发布更新", "/公告状态", "/公告失败", "/重试公告",
                         "/邀请列表", "/同意邀请"}
        if parts[0] in info_commands:
            try:
                with self.db.begin_nested():
                    return self.execute_information(parts, event, room)
            except AnnouncementError as exc:
                return str(exc)
        auction_commands = {"/拍卖场", "/拍卖", "/寄拍", "/拍", "/跟", "/拍卖详情",
                            "/我的拍卖", "/我的出价", "/拍卖记录", "/领取拍卖马", "/下架拍卖"}
        if parts[0] in auction_commands:
            try:
                with self.db.begin_nested():
                    return self.execute_auction(parts, event, room)
            except GameError as exc:
                return error(exc.code, **exc.details)
        if parts[0] == "/帮助" and len(parts) <= 2:
            return help_text(self.stage, parts[1] if len(parts) == 2 else None)
        if parts[0] in ("/铃露玩法", "/菜单"):
            if len(parts) > 2:
                return error("syntax")
            return menu_text(self.stage, parts[1] if len(parts) == 2 else None)
        factory_command = parts[0] in {"/加工", "/加工厂", "/配方", "/加工配方", "/投产", "/取货", "/加工厂升级", "/加急", "/取消投产"}
        horse_command = parts[0] in {"/马厩", "/买马", "/马", "/马匹命名", "/喂马", "/繁育",
                                     "/接生", "/血统", "/马厩升级", "/退役", "/马册"}
        farm_command = parts[0] in {"/农场", "/农田详情", "/种子商店", "/买种子", "/播种", "/农场升级",
                                    "/配制", "/马粮商店", "/买马粮", "/出售"}
        asset_command = parts[0] in {"/转账", "/管理员发币", "/管理员发道具"}
        if parts[0] == "/加工":
            parts = ["/加工厂", *parts[1:]] if len(parts) == 1 else ["/_parallel_process", *parts[1:]]
        inventory_panel = parts == ["/库存"]
        if inventory_panel:
            parts = ["/牧场", "查看"]
        ruihe_panel = False
        market_panel = False
        if parts[0] == "/牧场":
            ruihe_panel = True
            if len(parts) == 1:
                parts = ["/牧场", "查看"]
            elif len(parts) == 2 and parts[1].isdecimal():
                parts = ["/牧场", "查看", parts[1]]
        elif parts[0] == "/行情":
            if len(parts) != 1:
                return error("syntax")
            market_panel = True
        elif parts[0] == "/买动物":
            parts = ["/牧场", "购买", *parts[1:]]
        elif parts[0] in {"/收取", "/收获"}:
            parts = ["/统一收获", *parts[1:]]
        elif parts[0] == "/牧场升级":
            parts = ["/牧场", "升级", *parts[1:]]
        elif parts[0] == "/喂食":
            if len(parts) > 2 or (len(parts) == 2 and parts[1] != "精"):
                return error("syntax")
            parts = ["/牧场", "喂食", "全部", *(["精"] if len(parts) == 2 else [])]
        elif parts[0] == "/买精饲料":
            parts = ["/牧场", "购买", "精饲料", *parts[1:]]
        elif parts[0] == "/买入":
            if len(parts) not in (2, 3):
                return error("syntax")
            parts = ["/市场买入", *parts[1:]]
        elif parts[0] == "/资料":
            parts = ["/我的", *parts[1:]]
        elif parts[0] == "/注册":
            if len(parts) != 1:
                return error("register_syntax")
            parts = ["/加入"]
        reference = uid()
        try:
            # A rejected command rolls back *all* tentative changes, while the outer
            # transaction retains the inbox and its failure reply for idempotency.
            with self.db.begin_nested():
                service = GameService(self.db, self.now, self.secret, event.sender, event.room, reference)
                if asset_command:
                    service = AssetService(self.db, self.now, self.secret, event.sender, event.room, reference)
                elif factory_command:
                    service = FactoryService(self.db, self.now, self.secret, event.sender, event.room, reference)
                elif horse_command:
                    service = HorseService(self.db, self.now, self.secret, event.sender, event.room, reference)
                elif farm_command:
                    service = FarmService(self.db, self.now, self.secret, event.sender, event.room, reference)
                service.command_context = {"room": event.room, "message_id": event.message_id, "text": event.text}
                service.weather_enabled = self.weather_enabled
                result = self.execute(service, parts, event, room)
                if factory_command:
                    if result.get("kind") == "factory":
                        rendered = render_factory(result)
                    elif result.get("kind") == "factory_recipes":
                        rendered = render_factory_recipes(result, result.get("tier"))
                    else:
                        rendered = render(result, reference, self.stage)
                elif horse_command:
                    rendered = render_horse(result)
                elif farm_command:
                    rendered = render_farm(result) if result.get("kind") != "sell" else render(result, reference, self.stage)
                elif market_panel and result.get("kind") == "market":
                    rendered = render(result, reference, self.stage)
                elif inventory_panel and result.get("kind") == "ranch":
                    rendered = render_inventory(result)
                elif ruihe_panel and result.get("kind") == "ranch":
                    rendered = render_ranch(result, self.stage)
                else:
                    rendered = render(result, reference, self.stage)
                next_steps = {
                    "join": "/买动物 鸡 1 | /播种 燕麦 1 | /怎么玩",
                    "buy": "/牧场 | /库存", "sell": "/钱包 | /行情",
                    "upgrade": "/牧场", "feed": "/牧场 | /待办",
                    "harvest": "/库存 | /行情 | /出售 商品 数量",
                    "factory_collect": "/库存 | /行情 | /出售 商品 数量",
                    "factory_upgrade": "/配方 | /加工厂",
                    "factory_expedite": "/取货",
                    "farm_plant": "/农场 | /待办",
                    "farm_harvest": "/配制 | /出售 商品 数量 | /播种 燕麦 1",
                    "farm_upgrade": "/农场 | /种子商店",
                    "horse_buy": "/马厩 | /配方",
                    "horse_upgrade": "/马厩 | /买马",
                    "player_transfer": "/钱包 | /排行榜",
                }.get(result.get("kind"))
                if next_steps:
                    rendered += "\n下一步：" + next_steps
                events = WorldEventService(self.db, self.now, **self.world_event_policy)
                if result.get("kind") == "harvest":
                    events.record_harvest(event.sender, event.room, result.get("totals", {}))
                elif result.get("kind") == "factory_collect":
                    events.record_factory_critical(event.sender, event.room, result.get("results", []))
                with self.db.begin_nested() as tip_tx:
                    tip = GuidanceService(self.db, self.stage, self.now).maybe_tip(event.sender, original_command)
                    if tip and len(reply_pages(rendered + "\n" + tip)) > 1:
                        tip_tx.rollback()
                        tip = None
                return rendered + ("\n" + tip if tip else "")
        except GameError as exc:
            message = error(exc.code, **exc.details)
            if exc.code in {"syntax", "quantity", "sell_syntax", "transfer_syntax", "register_syntax",
                            "market_item", "factory_recipe"}:
                hint = command_hint(parts)
                if hint:
                    message += "\n" + hint
            if exc.code == "unknown":
                suggestion = self.suggest_command(event.text)
                if suggestion:
                    return f"🤔 没找到「{event.text.split()[0]}」\n你是不是想用：\n{suggestion}"
            return message
        except Exception:
            # Unexpected errors must roll back the outer inbox too, allowing a safe retry.
            log.exception("Game command failed before commit")
            raise

    def execute_information(self, parts, event, room=None):
        command = parts[0]
        admin = AdminLoginService(self.db, self.now, self.admin_login_password_hash,
                                  self.admins).is_admin(event.sender)
        announcements = AnnouncementService(self.db, self.now, event.sender)
        guidance = GuidanceService(self.db, self.stage, self.now)
        if command == "/更新" and len(parts) in (1, 2):
            return announcements.latest() if len(parts) == 1 else announcements.get_update(parts[1])
        if command == "/更新日志" and len(parts) in (1, 2):
            if len(parts) == 2 and admin:
                update = announcements._update(parts[1], published_only=True)
                return render_update(update, admin=True) if update else f"找不到已发布的 v{parts[1]} 更新。"
            return announcements.history()
        if command == "/怎么玩" and len(parts) in (1, 2):
            return guidance.how_to(parts[1] if len(parts) == 2 else None)
        if command == "/今日" and len(parts) == 1:
            return DailyBriefService(self.db, self.now, event.sender,
                                     weather_enabled=self.weather_enabled).render()
        if command == "/天气" and len(parts) == 1:
            from .weather_service import WeatherService
            from dzmm_bot.presentation.messages import render_weather
            return render_weather(WeatherService(self.db,self.now,enabled=self.weather_enabled).player_summary())
        if command == "/待办" and len(parts) == 1:
            return TodoService(self.db, event.sender, self.now).render()
        admin_commands = {"/创建更新", "/预览更新", "/发布更新", "/公告状态", "/公告失败", "/重试公告",
                          "/邀请列表", "/同意邀请"}
        if command in admin_commands and not admin:
            return "⚠️ 该指令仅限管理员使用。"
        if command in {"/邀请列表", "/同意邀请"}:
            if not room or room["kind"] != "private":
                return "⚠️ 请在 Bot 私聊中管理群聊邀请。"
            invitations = InviteApprovalService(self.db, self.now, event.sender)
            if command == "/邀请列表" and len(parts) == 1:
                return invitations.pending()
            if command == "/同意邀请" and len(parts) == 2:
                return invitations.approve(parts[1])
            return "指令格式：/邀请列表 | /同意邀请 <邀请ID>"
        if command == "/创建更新" and len(parts) == 2:
            return announcements.create_update(parts[1])
        if command == "/预览更新" and len(parts) == 2:
            return announcements.preview(parts[1])
        if command == "/发布更新" and len(parts) == 2:
            return announcements.publish(parts[1])
        if command == "/公告状态" and len(parts) == 2:
            return announcements.status(parts[1])
        if command == "/公告失败" and len(parts) == 2:
            return announcements.failed_rooms(parts[1])
        if command == "/重试公告" and len(parts) == 2:
            return announcements.retry_failed(parts[1])
        if command in admin_commands:
            return "指令格式：/创建更新 版本号 | /预览更新 版本号 | /发布更新 版本号"
        return error("syntax")

    def execute_auction(self, parts, event, room):
        command = parts[0]
        if self.stage != "full":
            raise GameError("unavailable")
        if self.whitelist and event.sender not in self.whitelist:
            raise GameError("whitelist")
        admin = AdminLoginService(self.db, self.now, self.admin_login_password_hash,
                                  self.admins).is_admin(event.sender)
        service = AuctionService(self.db, self.now, self.secret, event.sender, event.room, uid())
        service.start_cooldown_seconds = self.auction_cooldown_seconds
        service.command_context = {"room": event.room, "message_id": event.message_id, "text": event.text}
        if parts == ["/拍卖场"]:
            return service.panel()
        if parts == ["/拍卖详情"]:
            return service.detail()
        if parts == ["/我的拍卖"]:
            return service.mine()
        if parts == ["/我的出价"]:
            return service.my_bids()
        if parts == ["/拍卖记录"]:
            return service.history()
        if command == "/领取拍卖马" and len(parts) in (1, 2):
            return service.claim_horse(parts[1] if len(parts) == 2 else None)
        if command == "/下架拍卖" and len(parts) == 1:
            if not admin:
                raise GameError("admin_only")
            return service.cancel()
        if room["kind"] != "group":
            raise GameError("auction_group")
        if command == '/拍卖':
            if len(parts)==4 and parts[1]=='马':
                return service.start_asset(parts[2],1,price_cents(parts[3]),admin=admin,horse=True)
            if len(parts)==5 and parts[1] in {'道具','物品'}:
                return service.start_item(parts[2],quantity(parts[3]),price_cents(parts[4]),admin=admin)
            if len(parts) in (3,4):
                return service.start_asset(parts[1],quantity(parts[2]) if len(parts)==4 else 1,
                    price_cents(parts[-1]),admin=admin)
            raise GameError('auction_syntax')
        if command == "/寄拍" and len(parts) == 4 and parts[1] == "马":
            return service.start_horse(parts[2], price_cents(parts[3]), admin=admin)
        if command == "/寄拍" and len(parts) == 5 and parts[1] == "道具":
            return service.start_item(parts[2], quantity(parts[3]), price_cents(parts[4]), admin=admin)
        if command in {"/拍", "/跟"} and len(parts) in (1, 2):
            return service.bid(price_cents(parts[1]) if len(parts) == 2 else None)
        raise GameError("auction_syntax")

    @staticmethod
    def suggest_command(text):
        parts = text.strip().split()
        if not parts:
            return None
        typed = parts[0].lstrip("/")
        if typed in {"投厂", "投放"}:
            return "/投产" + (" " + " ".join(parts[1:]) if len(parts) > 1 else "")
        candidates = ("/帮助", "/钱包", "/签到", "/牧场", "/行情", "/买入", "/买动物", "/收取", "/喂食",
                      "/出售", "/牧场升级", "/加工厂", "/配方", "/加工配方", "/投产", "/取货", "/加急", "/取消投产",
                      "/马厩", "/买马", "/马", "/马匹命名", "/马厩升级", "/血统", "/马册",
                      "/农场", "/种子商店", "/买种子", "/播种", "/收获", "/配制", "/买马粮",
                      "/怎么玩", "/今日", "/天气", "/待办", "/更新", "/更新日志",
                      "/拍卖场", "/拍卖", "/寄拍", "/拍", "/跟", "/拍卖详情", "/拍卖记录")
        ranked = sorted(((SequenceMatcher(None, typed, candidate[1:]).ratio(), candidate)
                         for candidate in candidates), reverse=True)
        if not ranked or ranked[0][0] < 0.5 or abs(len(typed) - len(ranked[0][1]) + 1) > 1:
            return None
        return ranked[0][1] + (" " + " ".join(parts[1:]) if len(parts) > 1 else "")

    def execute(self, service, p, event, room):
        if p[0] in {'/发红包','/抢红包'}:
            if self.stage != 'full':
                raise GameError('unavailable')
            if room['kind'] != 'group':
                raise GameError('red_packet_group')
            from .red_packet_service import RedPacketService
            packet=RedPacketService(self.db,self.now,self.secret,event.sender,event.room,service.reference)
            if p[0]=='/发红包':
                if len(p) not in (2,3,4):
                    raise GameError('red_packet_syntax')
                try:
                    amount=coin_amount(p[1])
                except GameError:
                    raise GameError('red_packet_amount') from None
                count=quantity(p[2]) if len(p)>=3 else 10
                try:
                    duration=quantity(p[3]) if len(p)==4 else 2
                except GameError:
                    raise GameError('red_packet_duration') from None
                return packet.send(amount,count,duration)
            if len(p)!=1:
                raise GameError('red_packet_syntax')
            return packet.claim()
        if p == ['/统一收获']:
            if self.stage == 'm0':
                raise GameError('unavailable')
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError('whitelist')
            result=service.harvest()
            if self.stage == 'full':
                # The ranch harvest already records an event using service.reference.
                # A ready farm harvest records a second event, so it needs its own ID
                # to avoid colliding with the events table primary key and rolling
                # back the entire combined harvest transaction.
                farm_service=FarmService(self.db,self.now,self.secret,event.sender,event.room,uid())
                farm_service.command_context=service.command_context
                farm_service.weather_enabled=service.weather_enabled
                farm=farm_service.harvest()
                result['farm_totals']=farm['totals']
                result['farm_growing']=farm['growing']
            return result
        if p[0] in {"/放养", "/回收"}:
            if self.stage != "full":
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
        if p[0] == "/放养":
            from dzmm_bot.domain.animal_rarity import SHINY_ANIMAL_ITEMS
            if len(p) != 3 or p[1] not in SHINY_ANIMAL_ITEMS:
                raise GameError("syntax")
            return service.release_shiny_animals(SHINY_ANIMAL_ITEMS[p[1]], quantity(p[2]))
        if p[0] == "/回收":
            if len(p) != 3 or p[1] not in {"鸡", "羊", "牛"}:
                raise GameError("syntax")
            return service.recycle_animal(ANIMALS[p[1]], quantity(p[2]))
        if p[0] == "/转账":
            if room["kind"] != "group":
                raise GameError("transfer_group")
            if len(p) != 2:
                raise GameError("transfer_syntax")
            return service.transfer(getattr(event, "referenced_sender_id", None),
                                    getattr(event, "referenced_message_id", None), coin_amount(p[1]))
        if p[0] in {"/管理员发币", "/管理员发道具"}:
            if not AdminLoginService(self.db, self.now, self.admin_login_password_hash,
                                     self.admins).is_admin(event.sender):
                raise GameError("admin_only")
            if room["kind"] != "private":
                raise GameError("admin_private")
            if p[0] == "/管理员发币" and len(p) == 2:
                return service.grant_coins(coin_amount(p[1]))
            if p[0] == "/管理员发道具" and len(p) == 3:
                return service.grant_item(p[1], quantity(p[2]))
            raise GameError("syntax")
        if p[0] in {"/农场", "/农田详情", "/种子商店", "/买种子", "/播种", "/收获", "/农场升级",
                    "/配制", "/马粮商店", "/买马粮", "/出售"}:
            if self.stage != "full":
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
            if p == ["/农场"]:
                return service.panel()
            if p == ["/农田详情"]:
                return {**service.panel(), "kind": "farm_details"}
            if p == ["/种子商店"]:
                return service.seed_shop()
            if p[0] == "/买种子" and len(p) == 3 and p[1] in CROP_NAMES:
                return service.buy_seeds(CROP_NAMES[p[1]], quantity(p[2]))
            if p[0] == "/播种" and len(p) in (2, 3) and p[1] in CROP_NAMES:
                return service.plant(CROP_NAMES[p[1]], quantity(p[2]) if len(p) == 3 else 1)
            if p == ["/收获"]:
                return service.harvest()
            if p == ["/农场升级"]:
                return service.upgrade_farm()
            if p == ["/配制"]:
                return {**service.recipes(), "command_hint": command_hint(p)}
            if p[0] == "/配制" and len(p) in (2, 3) and p[1] in FEED_NAMES:
                return service.mix(FEED_NAMES[p[1]], quantity(p[2]) if len(p) == 3 else 1)
            if p == ["/马粮商店"]:
                if not AdminLoginService(self.db, self.now, self.admin_login_password_hash,
                                         self.admins).is_admin(event.sender):
                    raise GameError("admin_only")
                return service.feed_shop()
            if p[0] == "/买马粮" and len(p) in (2, 3) and p[1] in FEED_NAMES:
                return service.buy_feed(FEED_NAMES[p[1]], quantity(p[2]) if len(p) == 3 else 1)
            if p[0] == "/出售":
                if len(p) != 3 or p[1] == "全部":
                    raise GameError("sell_syntax")
                code = CROP_NAMES.get(p[1]) or PRODUCTS.get(p[1])
                if not code:
                    raise GameError("market_item", name=p[1])
                return service.sell_any(code, quantity(p[2]))
            raise GameError("syntax")
        if p[0] in {"/马厩", "/买马", "/马", "/马匹命名", "/喂马", "/繁育",
                    "/接生", "/血统", "/马厩升级", "/退役", "/马册"}:
            if self.stage != "full":
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
            if p == ["/马厩"]:
                return service.panel()
            if p == ["/买马"]:
                return service.buy_horse()
            if p[0] == "/马" and len(p) == 2:
                return service.detail(p[1])
            if p[0] == "/马匹命名" and len(p) in (2, 3):
                return service.rename(p[-1], p[1] if len(p) == 3 else None)
            if p == ["/马厩升级"]:
                return service.upgrade_stable()
            if p[0] == "/退役" and len(p) == 2:
                return service.retire(p[1])
            if p == ["/马册"]:
                return service.studbook()
            if p[0] == "/血统" and len(p) == 2:
                return service.lineage(p[1])
            if p[0] == "/喂马" and len(p) == 3:
                return service.feed_horse(p[1], FEED_NAMES.get(p[2]))
            if p[0] == "/繁育" and len(p) == 3:
                return service.breed_horses(p[1], p[2])
            if p == ["/接生"]:
                return service.deliver_foals()
            raise GameError("syntax")
        if p[0] in {"/加工厂", "/配方", "/加工配方", "/投产", "/_parallel_process", "/取货", "/加工厂升级", "/加急", "/取消投产"}:
            if self.stage != "full":
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
            if p[0] == "/加工厂" and len(p) == 1:
                return service.panel()
            if p == ["/配方"]:
                return {**service.panel(), "kind": "factory_recipes", "tier": 1}
            if p[0] == "/配方" and len(p) == 2 and p[1].upper() in {"1", "2", "3", "T1", "T2", "T3"}:
                return {**service.panel(), "kind": "factory_recipes", "tier": int(p[1][-1])}
            if p[0] == "/配方" and len(p) == 2 and p[1] in RECIPE_NAMES:
                return {**service.panel(), "kind": "factory_recipes", "recipe_id": RECIPE_NAMES[p[1]]}
            if p[0] == "/加工配方" and len(p) in (1, 2):
                if len(p) == 2 and p[1].upper() not in {"1", "2", "3", "T1", "T2", "T3"}:
                    raise GameError("syntax")
                return {**service.panel(), "kind": "factory_recipes",
                        "tier": int(p[1][-1]) if len(p) == 2 else 1}
            if p[0] == "/_parallel_process" and len(p) == 3 and p[2].isdecimal():
                recipe_id = p[1] if p[1] in RECIPES else RECIPE_NAMES.get(p[1])
                if not recipe_id:
                    raise GameError("factory_recipe", name=p[1])
                return service.start_repeated(recipe_id, quantity(p[2]))
            if p[0] in {"/投产", "/_parallel_process"} and len(p) >= 2:
                recipe_ids = [token if token in RECIPES else RECIPE_NAMES.get(token)
                              for token in p[1:]]
                if len(p) > 3 or (len(p) >= 3 and all(recipe_ids)):
                    if not all(recipe_ids):
                        invalid = next(token for token, recipe_id in zip(p[1:], recipe_ids) if not recipe_id)
                        raise GameError("factory_recipe", name=invalid)
                    return service.start_many(recipe_ids)
                recipe_id = recipe_ids[0]
                if not recipe_id:
                    raise GameError("factory_recipe", name=p[1])
                if p[0] == "/_parallel_process":
                    return service.start(recipe_id, 1)
                return service.start(recipe_id, quantity(p[2]) if len(p) == 3 else 1)
            if p == ["/取货"]:
                return service.collect_factory()
            if p == ["/加工厂升级"]:
                return service.upgrade_factory()
            if p in (["/加工厂升级", "核心"], ["/加工厂升级", "自动化核心"]):
                return service.upgrade_with_core()
            if p[0] == "/加急" and len(p) in (1, 2):
                return service.expedite(quantity(p[1]) if len(p) == 2 else None)
            if p[0] == "/取消投产" and len(p) in (1, 2):
                return service.cancel(quantity(p[1]) if len(p) == 2 else None)
            raise GameError("syntax")
        if p[0] in {"/补货", "/使用"}:
            if self.stage != "full":
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
            if len(p) != 2 or p[1] not in BUFF_NAMES:
                raise GameError("syntax")
            item = BUFF_NAMES[p[1]]
            return service.buffs.craft_buff_item(item) if p[0] == "/补货" else service.buffs.activate(item)
        if p[0] in ("/牧场", "/排行"):
            if self.stage == "m0" or (self.stage == "ranch" and len(p) > 1 and p[1] in ("出售", "升级")):
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
        if p[0] == "/加入":
            if len(p) != 1:
                raise GameError('register_syntax')
            if not event.name.strip():
                raise GameError('register_name_unavailable')
            return service.join(name_escape(event.name.strip()), room["kind"])
        if p[0] == "/市场买入" and len(p) in (2, 3):
            code = PRODUCTS.get(p[1])
            if code in (None, "all"):
                raise GameError("market_item", name=p[1])
            return service.market_buy(code, quantity(p[2]) if len(p) == 3 else 1)
        if p == ["/行情"]:
            return service.market()
        if p == ["/我的"]:
            return service.profile()
        if p == ["/钱包"]:
            return service.wallet()
        if p == ["/签到"]:
            result = service.check_in()
            result["todo"] = TodoService(self.db, event.sender, self.now).render()
            return result
        if p == ["/救济"]:
            return service.relief()
        if p[0] == "/排行":
            if len(p) == 1:
                p = ["/排行", "总榜"]
            elif len(p) == 2 and p[1].isascii() and p[1].isdigit():
                p = ["/排行", "总榜", p[1]]
            if len(p) not in (2, 3) or p[1] != "总榜":
                raise GameError("syntax")
            service.account()
            return service.ranking(quantity(p[2]) if len(p) == 3 else 1)
        if p[0] != "/牧场":
            raise GameError("unknown")
        if len(p) in (2, 3) and p[1] == "查看":
            return service.view(quantity(p[2]) if len(p) == 3 else 1)
        if len(p) in (3, 4) and p[1] == "购买" and p[2] in ANIMALS:
            return service.buy(ANIMALS[p[2]], quantity(p[3]) if len(p) == 4 else 1)
        if len(p) in (3, 4) and p[1] == "喂食":
            if p[2] == "全部":
                if len(p) == 4 and p[3] != "精":
                    raise GameError("syntax")
                return service.feed(premium=len(p) == 4)
            if len(p) == 3:
                return service.feed(p[2])
        if p == ["/牧场", "收获"]:
            return self.execute(service,['/统一收获'],event,room)
        if p == ["/牧场", "升级"]:
            return service.upgrade()
        if len(p) >= 2 and p[1] == "出售":
            if len(p) != 4 or p[2] == "全部":
                raise GameError("sell_syntax")
            if p[2] not in PRODUCTS:
                raise GameError("market_item", name=p[2])
            return service.sell(PRODUCTS[p[2]], quantity(p[3]))
        raise GameError("syntax")
