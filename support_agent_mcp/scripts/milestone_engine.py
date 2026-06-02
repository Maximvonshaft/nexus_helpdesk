from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# --- 配置区 ---
TIMEZONE_OFFSET_MAP = {
    1: 2,      # Europe (e.g., Albania)
    8: 8,      # China (CST)
    None: 0,
}

DEFAULT_TZ_OFFSET = 0

def parse_iso_time(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        if len(s) == 16:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
        else:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=None)
    except ValueError:
        return None

def to_utc(dt: datetime, tz_offset: int) -> datetime:
    return dt.replace(tzinfo=None) - timedelta(hours=tz_offset)

# --- 核心里程碑定义 (全生命周期) ---
ORIGIN_MILESTONES = {
    "pickup": {"action": "1"},
    "inbound": {"action": "150"},
    "assembling": {"action": "181"},
    "outbound": {"action": "190"},
    "domestic_handover": {"action": "191"}
}

FLIGHT_MILESTONES = {
    "flight_departure": {"action": "220"},
    "flight_arrival": {"action": "230"}
}

CUSTOMS_MILESTONES = {
    "customs_started": {"action": "360", "subAction": "360"},
    "customs_inspection": {"action": "360", "subAction": "36003"},
    "customs_completed": {"action": "370", "subAction": "370"},
    "customs_exception": {"action": "401"},
}

LASTMILE_MILESTONES = {
    "dc_arrival": {"action": "375"},
    "delivery_out": {"action": "4"},
    "delivered": {"action": "5"},
    "pickup_point": {"action": "18"}
}

ALL_MILESTONES = {**ORIGIN_MILESTONES, **FLIGHT_MILESTONES, **CUSTOMS_MILESTONES, **LASTMILE_MILESTONES}

def classify_track(track: dict) -> Optional[str]:
    action = str(track.get("action", ""))
    sub = str(track.get("subAction", ""))
    for name, spec in ALL_MILESTONES.items():
        if action == spec.get("action") and sub == spec.get("subAction", sub):
            return name
        if action == spec.get("action") and not spec.get("subAction"):
            return name
    return None

def extract_timeline(tracks: List[dict]) -> List[Dict[str, Any]]:
    timeline = []
    for i, t in enumerate(tracks):
        ts_str = t.get("time")
        tz_off = t.get("timezone")
        dt = parse_iso_time(ts_str)
        if not dt:
            continue
        utc_dt = to_utc(dt, TIMEZONE_OFFSET_MAP.get(tz_off, DEFAULT_TZ_OFFSET))
        milestone = classify_track(t)
        timeline.append({
            "index": i,
            "raw": t,
            "time_local": ts_str,
            "time_utc": utc_dt.isoformat(),
            "milestone": milestone,
            "action": t.get("action"),
            "subAction": t.get("subAction"),
            "actionName": t.get("actionName"),
            "message": t.get("message") or t.get("msgEng") or t.get("msgLoc"),
            "scanSource": t.get("scanSource"),
        })
    timeline.sort(key=lambda x: x["time_utc"])
    return timeline

def _get_time(timeline: List[dict], milestones: List[str], reverse=False) -> Optional[datetime]:
    iterable = reversed(timeline) if reverse else timeline
    for t in iterable:
        if t["milestone"] in milestones:
            return datetime.fromisoformat(t["time_utc"])
    return None

def compute_lifecycle_durations(timeline: List[dict]) -> Dict[str, float]:
    durations = {}
    
    # 1. 始发地段 (Origin)
    origin_start = _get_time(timeline, ["pickup", "inbound"])
    origin_end = _get_time(timeline, ["flight_departure", "domestic_handover"], reverse=True)
    if origin_start and origin_end and origin_end > origin_start:
        durations["origin_hours"] = (origin_end - origin_start).total_seconds() / 3600

    # 2. 国际干线段 (Flight Transit)
    flight_dep = _get_time(timeline, ["flight_departure"])
    flight_arr = _get_time(timeline, ["flight_arrival"])
    if flight_dep and flight_arr and flight_arr > flight_dep:
        durations["flight_hours"] = (flight_arr - flight_dep).total_seconds() / 3600

    # 3. 目的国清关段 (Customs)
    customs_st = _get_time(timeline, ["customs_started"])
    customs_cmp = _get_time(timeline, ["customs_completed"])
    if customs_st and customs_cmp and customs_cmp > customs_st:
        durations["customs_hours"] = (customs_cmp - customs_st).total_seconds() / 3600

    # 3.5 查验子段 (Inspection)
    insp = _get_time(timeline, ["customs_inspection"])
    if insp and customs_cmp and customs_cmp > insp:
        durations["inspection_hours"] = (customs_cmp - insp).total_seconds() / 3600

    # 4. 末端派送段 (Last Mile)
    dc_arr = _get_time(timeline, ["dc_arrival", "customs_completed"])
    deliv = _get_time(timeline, ["delivered", "pickup_point"])
    if dc_arr and deliv and deliv > dc_arr:
        durations["last_mile_hours"] = (deliv - dc_arr).total_seconds() / 3600

    # Total Transit
    if timeline:
        first = datetime.fromisoformat(timeline[0]["time_utc"])
        last = datetime.fromisoformat(timeline[-1]["time_utc"])
        durations["total_transit_hours"] = (last - first).total_seconds() / 3600

    return durations

def generate_ai_empowerment(timeline: List[dict], durations: dict) -> Dict[str, str]:
    """生成给AI客服的“推客/安抚”策略武器"""
    latest = timeline[-1] if timeline else {}
    ms = latest.get("milestone")
    
    stage = "UNKNOWN"
    strategy = ""
    
    if ms in LASTMILE_MILESTONES:
        stage = "LAST_MILE (末端派送)"
        if ms == "delivered":
            strategy = "包裹已送达。如果客户说没收到，请提示核实签收人名字或代收点，并主动表示可以协助网点核查。"
        elif ms == "delivery_out":
            strategy = "包裹正在派送员手上！请主动提醒客户保持电话畅通，制造期待感。"
        else:
            strategy = "已进入目的国末端网络。距离送达仅剩最后一步，请安抚客户即将收到。"
            
    elif ms in CUSTOMS_MILESTONES:
        stage = "CUSTOMS (清关中)"
        ch = durations.get("customs_hours", 0)
        if ms == "customs_exception":
            strategy = "⚠️ 清关异常！请立刻主动询问客户是否收到海关补件通知，并引导其配合交税或提供文件。"
        elif ms == "customs_inspection":
            strategy = "海关正常查验中。这是目的国政策不可控因素，非我司物流慢。请用此理由安抚客户耐心等待。"
        else:
            strategy = "包裹正排队清关。请向客户普及当地清关通常需要的时间，化解焦虑。"
            
    elif ms in FLIGHT_MILESTONES:
        stage = "FLIGHT_TRANSIT (国际空运)"
        strategy = "包裹已离境或刚落地，这通常是物流闭环中最平稳的阶段。可以告知客户包裹正在跨越国境。"
        
    elif ms in ORIGIN_MILESTONES:
        stage = "ORIGIN (始发地处理)"
        strategy = "包裹刚发出不久，正在国内/始发仓分拨。请告知客户我们正在全速处理将其送上飞机。"
        
    else:
        stage = "PROCESSING (处理中)"
        strategy = "根据最新轨迹合理安抚。"

    # 寻找耗时最长的瓶颈，赋能 AI 解释
    bottleneck = ""
    max_h = 0
    for k, v in durations.items():
        if k in ["origin_hours", "flight_hours", "customs_hours", "last_mile_hours"]:
            if v > max_h:
                max_h = v
                bottleneck = k
                
    explanation_ammo = ""
    if max_h > 72: # 某一段超过3天
        if bottleneck == "customs_hours":
            explanation_ammo = f"本次物流主要耗时在【海关环节】({max_h:.1f}小时)。这是极好的免责话术，请明确告知客户物流网络很快，是海关抽查导致的时效拉长。"
        elif bottleneck == "flight_hours":
            explanation_ammo = f"本次物流主要耗时在【航空排期/飞行】({max_h:.1f}小时)。可向客户解释近期国际航班资源紧张或航线较长。"
        elif bottleneck == "last_mile_hours":
            explanation_ammo = f"本次主要耗时在【当地末端派送】({max_h:.1f}小时)。可解释当地网点爆仓或地址偏远。"

    return {
        "current_stage": stage,
        "actionable_strategy": strategy,
        "bottleneck_ammo": explanation_ammo
    }

def analyze_tracking_payload(data: dict) -> dict:
    root = data or {}
    if "decrypted_response" in root:
        root = root["decrypted_response"]
    elif "raw" in root and "data" in root["raw"]:
        root = root["raw"]
        
    if "data" in root and isinstance(root["data"], list) and len(root["data"]) > 0:
        item = root["data"][0]
    elif "tracks" in root:
        item = root
    elif "data" in root and isinstance(root["data"], dict):
        item = root["data"]
    else:
        item = root

    tracks = item.get("tracks") or []
    timeline = extract_timeline(tracks)
    
    # NEW API COMPATIBILITY: If no tracks but we have a raw status
    if not timeline and "status" in item:
        action_code = str(item.get("status"))
        now_iso = datetime.now(timezone.utc).isoformat()
        timeline = [{
            "index": 0,
            "raw": item,
            "time_local": item.get("estimatedDeliveryTime") or "",
            "time_utc": now_iso,
            "milestone": classify_track({"action": action_code}),
            "action": action_code,
            "subAction": "",
            "actionName": "", # Will be filled by status_mapper
            "message": "",
            "scanSource": "api_v2"
        }]

    if not timeline:
        return {}

    durations = compute_lifecycle_durations(timeline)
    empowerment = generate_ai_empowerment(timeline, durations)
    tracking_no = item.get("mailNo") or item.get("trackingNo") or item.get("waybillCode") or "Unknown"

    latest = timeline[-1]
    
    # 构建对客结构 (事实)
    customer_answer = (
        f"✅ 最新状态: {latest.get('actionName')} ({latest.get('time_local')})\n"
        f"全链路耗时洞察:\n"
        f"- 始发地处理: {durations.get('origin_hours',0):.1f} 小时\n"
        f"- 国际干线: {durations.get('flight_hours',0):.1f} 小时\n"
        f"- 目的国清关: {durations.get('customs_hours',0):.1f} 小时\n"
        f"- 末端派送: {durations.get('last_mile_hours',0):.1f} 小时\n"
    )

    # 构建对内/AI策略 (武器库)
    internal_summary = (
        f"【AI 客服话术赋能 (AI Empowerment)】\n"
        f"📍 当前阶段: {empowerment['current_stage']}\n"
        f"🎯 行动策略: {empowerment['actionable_strategy']}\n"
        f"🛡️ 免责/解释弹药: {empowerment['bottleneck_ammo']}\n"
    )

    # 简单风险
    escalate = False
    if latest.get("action") == "401" or (durations.get("customs_hours", 0) > 240):
        escalate = True

    return {
        "tracking_no": tracking_no,
        "timeline": timeline,
        "durations": durations,
        "risk": {"escalate_required": escalate},
        "customer_answer": customer_answer,
        "internal_summary": internal_summary,
        "ai_empowerment": empowerment
    }
