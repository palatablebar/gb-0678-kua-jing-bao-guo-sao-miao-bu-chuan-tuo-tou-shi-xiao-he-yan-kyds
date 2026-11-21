"""月末抽检 HTTP 接口。

GET  /api/sampling?run_id=..            清单版本链 + 当前已确认（可执行）版本
POST /api/sampling/drafts               建/重算待确认清单（补传后产生新版本，旧版保留）
POST /api/sampling/confirm              确认某版本，提升为该批唯一可执行清单
POST /api/sampling/claim                客服在已确认清单上领取样本（幂等、不可抢占）
"""
from flask import Blueprint,request,jsonify
from app.errors import DomainError
from app import store,sampling_store as ss

bp=Blueprint("sampling",__name__)

def _resolve_run_id(payload):
    run_id=(payload or {}).get("run_id")
    if not run_id:
        latest=store.latest()
        if not latest: raise DomainError("NO_RUN","尚无批次，请先建立扫描批次",404)
        run_id=latest["run_id"]
    else:
        store.get(run_id)  # 校验存在，不存在抛 404
    return run_id

@bp.route("/api/sampling",methods=["GET"])
def overview():
    run_id=request.args.get("run_id")
    if not run_id:
        latest=store.latest()
        if not latest: return {"run_id":None,"versions":[],"active":None}
        run_id=latest["run_id"]
    return {"run_id":run_id,"versions":ss.list_versions(run_id),"active":ss.get_active(run_id)}

@bp.route("/api/sampling/drafts",methods=["POST"])
def drafts():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    run_id=_resolve_run_id(payload)
    candidate=payload.get("candidate_run_id",run_id)
    if not isinstance(candidate,str): raise DomainError("BAD_CANDIDATE","候选批次标识无效")
    size=payload.get("sample_size")
    if not isinstance(size,int) or isinstance(size,bool): raise DomainError("BAD_SAMPLE_SIZE","请提供整数抽检数量")
    return jsonify(ss.create_or_refresh_draft(run_id,candidate,size))

@bp.route("/api/sampling/confirm",methods=["POST"])
def confirm():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    run_id=_resolve_run_id(payload)
    seq=payload.get("seq")
    if not isinstance(seq,int): raise DomainError("BAD_SEQ","请提供版本序号")
    return jsonify(ss.confirm(run_id,seq))

@bp.route("/api/sampling/claim",methods=["POST"])
def claim():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): raise DomainError("BAD_BODY","请求需要对象")
    run_id=_resolve_run_id(payload)
    parcel=payload.get("parcel")
    if not isinstance(parcel,str) or not parcel.strip(): raise DomainError("BAD_PARCEL","请提供包裹号")
    return jsonify(ss.claim(run_id,parcel.strip(),payload.get("claimed_by")))
