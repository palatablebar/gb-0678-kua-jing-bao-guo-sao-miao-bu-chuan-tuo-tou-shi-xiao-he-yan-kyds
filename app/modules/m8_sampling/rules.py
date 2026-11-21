"""月末超时包裹抽检规则（纯函数，无副作用）。

裁决要点：
- 抽检池为时效模块判定为“超时”的包裹，按包裹号确定排序，固定数量抽取，可重放。
- 补传改变超时异常集合时，不覆盖任何已确认清单；只产出“待确认”的新版本。
- 客服已领取的样本在新清单中必须保留；若该样本已不在最新超时集合中，重抽被阻断，
  交人工处置，绝不静默替换。
- 一次重抽只做等量替换：保留位之外按确定性顺序从新异常池补齐，样本数恒定、无重复。
"""
from app.errors import DomainError

REASONS={
    "NEW_POOL":"新建抽检清单",
    "RETAINED":"保留原清单样本",
    "CLAIMED_RETAINED":"客服已领取，锁定保留",
    "ENTER_LATE":"补传后新判超时，补位",
    "NO_LONGER_LATE":"补传后已达标，移出抽检池",
    "NOT_SAMPLED":"原未抽中，随补传移出",
}
# 状态机：草稿可反复重算；只有“已确认”版本是可执行（归档/领取）版本。
STATUSES=("待确认","已确认","已归档")


def overdue_pool(timeliness_rows):
    """从时效模块输出中确定地抽取超时包裹池（字符串业务键，保留前导零）。"""
    return sorted(str(r["parcel"]) for r in timeliness_rows if r.get("sla")=="超时")


def _require_quota(pool, sample_size):
    if not isinstance(sample_size,int) or isinstance(sample_size,bool) or sample_size<=0:
        raise DomainError("BAD_SAMPLE_SIZE","抽检数量必须为正整数")
    if sample_size>len(pool):
        raise DomainError("POOL_TOO_SMALL","抽检数量超过超时包裹池容量")
    return pool


def build_draft(timeliness_rows, sample_size):
    """对一批时效结果建立首版待确认清单（确定性、固定数量）。"""
    pool=_require_quota(overdue_pool(timeliness_rows), sample_size)
    selected=pool[:sample_size]
    return {
        "sample_size":sample_size,
        "samples":[{"parcel":p,"change":"NEW_POOL","reason":REASONS["NEW_POOL"],"claimed_by":None} for p in selected],
        "diff":{"before":[], "after":list(selected),
                "added":[{"parcel":p,"reason":REASONS["NEW_POOL"]} for p in selected],
                "removed":[]},
        "pool_before":[],
        "pool_after":list(pool),
    }


def revise_draft(timeliness_rows, sample_size, active_samples):
    """补传后基于最新超时集合生成待确认新版本，旧的已确认清单保持不变。"""
    pool=overdue_pool(timeliness_rows)
    _require_quota(pool, sample_size)
    active=[dict(s) for s in active_samples]
    active_ids=[s["parcel"] for s in active]
    if len(active_ids)!=len(set(active_ids)):
        raise DomainError("SAMPLE_DUPLICATE","原抽检清单存在重复样本")
    pool_set=set(pool)
    # 已领取样本一旦因补传离开超时池，阻断重抽，禁止静默替换。
    for s in active:
        if s.get("claimed_by") and s["parcel"] not in pool_set:
            raise DomainError("CLAIMED_LEFT_POOL",
                              "客服已领取样本 %s 补传后不再超时，需人工处置后才能重抽" % s["parcel"], 409)
    retained=[s for s in active if s["parcel"] in pool_set]
    retained_set={s["parcel"] for s in retained}
    removed=[{"parcel":s["parcel"],
              "reason":REASONS["NO_LONGER_LATE"] if s["parcel"] not in pool_set else REASONS["NOT_SAMPLED"]}
             for s in active if s["parcel"] not in retained_set]
    fill=[p for p in pool if p not in retained_set]
    need=sample_size-len(retained)
    if need<0:
        raise DomainError("SAMPLE_SIZE_CHANGED","重抽不得改变固定抽检数量")
    if len(fill)<need:
        raise DomainError("POOL_TOO_SMALL","补传后超时池不足以补足固定抽检数量")
    chosen=fill[:need]
    new_samples=[{"parcel":s["parcel"],
                  "change":"CLAIMED_RETAINED" if s.get("claimed_by") else "RETAINED",
                  "reason":REASONS["CLAIMED_RETAINED"] if s.get("claimed_by") else REASONS["RETAINED"],
                  "claimed_by":s.get("claimed_by")} for s in retained]
    new_samples += [{"parcel":p,"change":"ENTER_LATE","reason":REASONS["ENTER_LATE"],"claimed_by":None}
                    for p in chosen]
    new_ids=[s["parcel"] for s in new_samples]
    if len(new_ids)!=sample_size or len(set(new_ids))!=sample_size:
        raise DomainError("SAMPLE_DUPLICATE","重抽结果数量或唯一性不满足约束")
    added=[{"parcel":p,"reason":REASONS["ENTER_LATE"]} for p in chosen]
    return {
        "sample_size":sample_size,
        "samples":new_samples,
        "diff":{"before":active_ids,"after":new_ids,"added":added,"removed":removed},
        "pool_before":sorted(set(active_ids)),
        "pool_after":list(pool),
    }


def changed(plan):
    return bool(plan["diff"]["added"] or plan["diff"]["removed"])
