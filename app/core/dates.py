"""日期语义：时区 Asia/Shanghai；周起始周一；解析失败不瞎填。"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings

TZ_NAME = "Asia/Shanghai"

_TIME_RE = re.compile(r"(?:(\d{1,2})[:：](\d{2})|(\d{1,2})点(?:(\d{1,2})分?)?)")


def tzinfo() -> ZoneInfo:
    return ZoneInfo(get_settings().TIMEZONE or TZ_NAME)


def now() -> datetime:
    return datetime.now(tzinfo())


def today() -> date:
    return now().date()


def week_range(d: date | None = None) -> tuple[date, date]:
    """返回本周 [monday, sunday]。"""
    d = d or today()
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def end_of_day(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=tzinfo())


def parse_relative_due(text: str) -> tuple[date | None, bool]:
    """解析「今天/明天/周五前/下周五/X月X日」等。

    Returns:
        (due_date or None, needs_confirm)
    """
    raw = text.strip()
    if not raw:
        return None, True

    t = today()
    if "今天" in raw:
        return t, False
    if "明天" in raw:
        return t + timedelta(days=1), False
    if "后天" in raw:
        return t + timedelta(days=2), False

    week_prefix = 0
    if "下下周" in raw:
        week_prefix = 2
    elif "下周" in raw:
        week_prefix = 1
    elif "本周" in raw:
        week_prefix = 0

    # 先在完整原文里找「周五/周五前」，避免去掉「下周」后只剩「五前」
    weekday_map = {
        "周一": 0,
        "周二": 1,
        "周三": 2,
        "周四": 3,
        "周五": 4,
        "周六": 5,
        "周日": 6,
        "周天": 6,
        "星期一": 0,
        "星期二": 1,
        "星期三": 2,
        "星期四": 3,
        "星期五": 4,
        "星期六": 5,
        "星期日": 6,
    }
    for name, idx in weekday_map.items():
        if name in raw:
            monday = t - timedelta(days=t.weekday())
            target = monday + timedelta(days=idx + week_prefix * 7)
            # 「周五前」含周五；本周目标日已过则顺延一周
            if week_prefix == 0 and target < t and "前" in raw:
                target += timedelta(days=7)
            return target, False

    # 形如 3月20日 / 2026-03-20 / 3/20
    for fmt in ("%Y-%m-%d", "%m月%d日", "%m/%d"):
        try:
            cleaned = raw.replace("前", "").strip()
            parsed = datetime.strptime(cleaned, fmt)  # noqa: DTZ007  date-only parse
            if parsed.year == 1900:
                return date(t.year, parsed.month, parsed.day), False
            return parsed.date(), False
        except ValueError:
            continue

    return None, True


def parse_remind_time(text: str) -> datetime | None:
    """解析自定义提醒时刻，支持相对与绝对两类表达。

    相对：「2分钟后 / 半小时后 / 1小时30分钟后」（相对当前时刻）
    绝对：「13:50 / 明天9点 / 10月3日 08:15 / 周五 14:00」

    绝对时间规则：
    - 只给时间（无日期词）：默认今天；该时刻已过则顺延到明天
    - 显式「今天 HH:MM」但已过：返回 None（让上层向用户确认，不瞎猜）
    - 过去日期 / 解析失败：返回 None
    返回带时区的 datetime；None = 无法解析或语义不成立。
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # 相对时间：N分钟后 / 半小时后 / 1小时30分钟后 / 一个半小时后
    # 「后」必须出现，避免把普通时长描述当提醒时刻；小时/分钟组件可分离出现
    if "后" in raw:
        cn_num = {"一": 1, "二": 2, "两": 2, "三": 3}
        m_h = re.search(r"(\d+(?:\.\d+)?|[一二两三])\s*个?半?小时", raw)
        m_half = re.search(r"半个?小时", raw)
        m_min = re.search(r"(\d+)\s*分钟后", raw)
        if m_h or m_half or m_min:
            minutes = 0.0
            if m_h:
                g = m_h.group(1)
                hours = float(g) if g.isdigit() or "." in g else cn_num.get(g, 0)
                minutes += hours * 60
            if m_half:
                minutes += 30
            if m_min:
                minutes += int(m_min.group(1))
            if minutes <= 0:
                return None
            return now() + timedelta(minutes=minutes)

    t = today()

    # 完整「日期+时间」格式优先
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H点%M分", "%m月%d日 %H:%M", "%m月%d日%H点%M分", "%m/%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)  # noqa: DTZ007
        except ValueError:
            continue
        d = dt.date() if dt.year != 1900 else date(t.year, dt.month, dt.day)
        if d < t:
            return None
        result = datetime.combine(d, time(dt.hour, dt.minute), tzinfo=tzinfo())
        if d == t and result <= now():
            return None
        return result

    m = _TIME_RE.search(raw)
    if not m:
        return None
    if m.group(1) is not None:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        hh, mm = int(m.group(3)), int(m.group(4) or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None

    # 日期部分：去掉时间片段后交给 parse_relative_due
    date_part = _TIME_RE.sub(" ", raw).strip()
    if date_part and date_part != raw:
        d, _needs = parse_relative_due(date_part)
        if d is None:
            return None
        # 周几已过本周 → 顺延一周
        if d < t and any(w in date_part for w in ("周", "星期")):
            d += timedelta(days=7)
    else:
        d = t

    if d < t:
        return None
    result = datetime.combine(d, time(hh, mm), tzinfo=tzinfo())
    if d == t and result <= now():
        if "今天" in raw:
            return None
        result += timedelta(days=1)  # 裸时间已过 → 明天
    return result
