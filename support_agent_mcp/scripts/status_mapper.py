STATUS_MAP = {
    "10": {
        "label": "已下单",
        "desc": "订单已创建，等待揽收",
        "action": "您可以等待快递员揽收，或联系客服修改信息",
    },
    "150": {
        "label": "已入库",
        "desc": "包裹已进入始发仓/分拨中心",
        "action": "正在处理中，请耐心等待发出",
    },
    "181": {
        "label": "已集包",
        "desc": "包裹已完成集包，等待后续干线处理",
        "action": "正在安排干线运输，请耐心等待",
    },
    "190": {
        "label": "已出库",
        "desc": "包裹已从仓库/分拨中心发出",
        "action": "即将进入下一运输阶段",
    },
    "191": {
        "label": "交接完成",
        "desc": "包裹已完成国内交接，准备进入干线运输",
        "action": "正在安排后续干线运输",
    },
    "220": {
        "label": "航班起飞",
        "desc": "包裹已进入干线航班运输",
        "action": "请等待航班到达及后续清关更新",
    },
    "230": {
        "label": "航班到达",
        "desc": "包裹已落地目的国家/地区",
        "action": "通常会进入清关或目的地分拨阶段",
    },
    "360": {
        "label": "清关中",
        "desc": "包裹正在进行清关处理",
        "action": "清关处理中，请耐心等待后续放行或派送更新",
    },
    "370": {
        "label": "清关完成",
        "desc": "包裹已完成清关",
        "action": "通常会尽快转入目的地仓或派送阶段",
    },
    "375": {
        "label": "到达中心仓",
        "desc": "包裹已到达目的地中心仓",
        "action": "通常会进入分拣或派送准备阶段",
    },
    "3750": {
        "label": "国际运输中",
        "desc": "包裹正运往目标国中",
        "action": "请耐心等待包裹抵达目的国",
    },
    "3751": {
        "label": "目标国已揽收",
        "desc": "包裹已到达目标国并被揽收",
        "action": "请等待后续清关和派送更新",
    },
    "2": {
        "label": "运输中",
        "desc": "包裹正在运输途中",
        "action": "请耐心等待包裹送达下一个节点",
    },
    "11": {
        "label": "待派送",
        "desc": "包裹已分配派送员",
        "action": "即将安排派送，请注意接听电话",
    },
    "730": {
        "label": "退件签收",
        "desc": "包裹已被发件人退回签收",
        "action": "如有疑问请联系发件人",
    },
    "-2": {
        "label": "异常签收",
        "desc": "包裹签收存在异常",
        "action": "需要人工介入核查",
    },
    "1": {
        "label": "收件扫描",
        "desc": "包裹已在当前网点完成收件/接收扫描",
        "action": "请等待后续派送或转运更新",
    },
    "4": {
        "label": "派送中",
        "desc": "快递员正在派送途中",
        "action": "请保持电话畅通，注意查收",
    },
    "5": {
        "label": "已签收",
        "desc": "包裹已成功送达",
        "action": "如有问题可随时联系我们",
    },
    "18": {
        "label": "自提件",
        "desc": "包裹已到达自提点",
        "action": "请尽快前往指定地点领取",
    },
    "401": {
        "label": "清关异常",
        "desc": "包裹在清关过程中遇到异常",
        "action": "需要人工协助处理",
    },
}


DANGER_TERMS = [
    "exception",
    "failed",
    "failure",
    "hold",
    "held",
    "rejected",
    "abnormal",
    "detained",
    "seized",
    "returned",
    "returning",
    "intercept",
    "unable to contact",
    "address issue",
    "delivery failed",
    "异常",
    "失败",
    "扣关",
    "退回",
    "退件",
    "拒收",
    "拦截",
    "查验异常",
    "地址异常",
    "联系不上",
    "派送失败",
]


CUSTOMS_EXCEPTION_TERMS = [
    "customs exception",
    "clearance exception",
    "held by customs",
    "customs hold",
    "清关异常",
    "海关扣留",
    "海关拦截",
]


def _combined_text(track: dict) -> str:
    return " ".join(
        [
            str(track.get("actionName", "")).lower(),
            str(track.get("message", "")).lower(),
            str(track.get("msgLoc", "")).lower(),
            str(track.get("msgEng", "")).lower(),
            str(track.get("msg", "")).lower(),
            str(track.get("subAction", "")).lower(),
        ]
    )


def fallback_status_text(track: dict) -> str:
    return (
        str(track.get("message") or "").strip()
        or str(track.get("msgLoc") or "").strip()
        or str(track.get("msgEng") or "").strip()
        or str(track.get("msg") or "").strip()
        or str(track.get("actionName") or "").strip()
    )


def should_escalate(track: dict) -> bool:
    action = str(track.get("action", "")).strip()
    haystack = _combined_text(track)

    if action == "401":
        return True

    if any(term in haystack for term in CUSTOMS_EXCEPTION_TERMS):
        return True

    return any(term in haystack for term in DANGER_TERMS)
