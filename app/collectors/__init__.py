"""官方渠道采集器。每个来源一个模块，统一输出岗位字典列表。"""

from . import bigtech, guopin, ncss, wechat, workday  # noqa: F401

REGISTRY = {
    bigtech.CODE: bigtech,
    guopin.CODE: guopin,
    ncss.CODE: ncss,
    wechat.CODE: wechat,
    workday.CODE: workday,
}

# 国聘搜索关键词。
# 用户明确要求：不限国企、不限职业，其他职业也要看 —— 所以这里分成"对口方向"和"通用岗位"两组，
# 通用组用来把池子做大，具体是不是合适交给匹配度排序去判断。
GUOPIN_CORE = [
    "数据分析", "数据运营", "商业分析", "数据分析师", "数据专员", "数据挖掘",
    "外贸", "外贸业务员", "外贸助理", "国际贸易", "进出口", "海外销售", "海外市场",
    "半导体", "半导体销售", "销售工程师", "客户经理", "石油", "化工", "能源",
]
# 跨境电商：用户点名要加的一整条线
CROSS_BORDER = [
    "跨境电商", "跨境电商BD", "跨境电商运营", "跨境电商销售", "亚马逊运营",
    "独立站运营", "海外仓", "跨境物流", "国际物流", "货代", "报关", "单证",
    "船务", "海外电商", "TikTok运营", "速卖通", "Shopee", "eBay", "海外推广",
    "选品", "海外商务拓展",
]
# BD / 商务拓展：用户点名要的
BD_KEYWORDS = [
    "BD", "商务拓展", "商务BD", "海外BD", "渠道拓展", "业务拓展", "客户开发",
    "商务经理", "BD经理", "市场拓展", "渠道经理", "合作拓展",
]
# 采购 / 供应链：用户点名要加的一整条线（他为此专门做了一版「国际采购」简历）。
# 单靠 GUOPIN_BROAD 里的"采购""供应链"两个泛词命中率太低，这里补上具体岗位名。
PROCUREMENT_SUPPLY = [
    "采购专员", "采购员", "采购助理", "采购工程师", "采购管理", "采购管培生",
    "国际采购", "招标采购", "战略采购", "寻源", "供应商开发", "供应商管理",
    "供应链", "供应链专员", "供应链助理", "供应链管培生", "供应链管理",
    "物流专员", "物流管理", "国际物流", "仓储管理", "库存管理",
    "生产计划", "物料计划", "计划员", "关务", "报关", "单证", "船务",
]
# 用户明确要求：不限职业、不限国企、池子越大越好 —— 这里覆盖校招常见岗位大类
GUOPIN_BROAD = [
    # 职能类
    "管培生", "储备干部", "人力资源", "行政", "财务", "会计", "审计", "法务",
    "采购", "供应链", "物流", "仓储", "计划", "统计", "翻译", "英语", "文员", "档案",
    # 市场与销售类
    "市场营销", "销售", "销售代表", "商务", "渠道", "招商", "运营", "电商运营",
    "客服", "客服专员", "新媒体", "文案", "品牌", "推广", "电子商务", "跟单",
    # 技术与生产类
    "项目管理", "质量管理", "生产管理", "安全管理", "环保", "设备管理",
    "技术员", "助理工程师", "工艺", "材料", "机械", "电气", "自动化",
    "仪表", "检验", "研发", "测试", "运维", "设计", "美工", "平面设计", "视频剪辑",
]
BASE_KEYWORDS = GUOPIN_CORE + CROSS_BORDER + BD_KEYWORDS + PROCUREMENT_SUPPLY + GUOPIN_BROAD
# 外企用工整的英文检索词
KEYWORDS_BY_SOURCE = {
    bigtech.CODE: BASE_KEYWORDS,
    guopin.CODE: BASE_KEYWORDS,
    ncss.CODE: BASE_KEYWORDS,
    wechat.CODE: wechat.KEYWORDS,
    workday.CODE: workday.KEYWORDS,
}
EXCLUDED_DIRECTIONS = ["环境设计", "室内设计", "景观", "美育"]
# 从简历意向里解析出来的无意义词元，不能当搜索词
STOP_WORDS = {"tob", "toc", "to", "b", "2b", "相关", "行业", "及", "类", "方向"}


def keywords_from_profile(profile: dict, source: str = guopin.CODE,
                          extra: list[str] | None = None) -> list[str]:
    if source != guopin.CODE:
        if source in (ncss.CODE, bigtech.CODE):
            return list(dict.fromkeys((extra or []) + BASE_KEYWORDS))
        if source == wechat.CODE:
            return list(dict.fromkeys((extra or []) + wechat.KEYWORDS))
        return list(dict.fromkeys((extra or []) + KEYWORDS_BY_SOURCE.get(source, BASE_KEYWORDS)))
    intent = str((profile.get("extra") or {}).get("job_intent") or "")
    words = [w.strip() for w in intent.replace("/", " ").replace("、", " ").split() if len(w.strip()) >= 2]
    words = [w for w in words
             if len(w) <= 6
             and w.lower() not in STOP_WORDS
             and not any(bad in w for bad in EXCLUDED_DIRECTIONS)
             and not all(ch.isascii() for ch in w)]
    merged = list(dict.fromkeys(
        words + GUOPIN_CORE + CROSS_BORDER + BD_KEYWORDS + PROCUREMENT_SUPPLY
        + (extra or []) + GUOPIN_BROAD))
    return merged


def available() -> list[dict]:
    return [{"code": module.CODE, "name": module.NAME, "note": module.NOTE,
             "supports": module.SUPPORTS}
            for module in REGISTRY.values()]
