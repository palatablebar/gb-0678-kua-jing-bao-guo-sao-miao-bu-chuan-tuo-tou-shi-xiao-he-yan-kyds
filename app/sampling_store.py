"""抽检清单持久层：版本化、只追加、事务保证。

- sampling_versions：每个批次(run)的清单版本，全部历史保留，不做物理删除。
  每批至多一条“已确认”（部分唯一索引），它就是该批唯一可执行核验样本。
- sample_claims：客服领取记录，追加写入；UNIQUE(run_id,parcel) 保证一样本只被领取一次。
- 补传只追加“待确认”新版本，绝不覆盖已确认/已领取数据；写入失败整体回滚。
"""
import json,sqlite3
from datetime import datetime,timezone
from app import store
from app.errors import DomainError
from app.modules.m8_sampling import rules

def connect():
    db=store.connect()
    db.execute("""CREATE TABLE IF NOT EXISTS sampling_versions(
        id TEXT PRIMARY KEY,run_id TEXT NOT NULL,base_run_id TEXT NOT NULL,seq INTEGER NOT NULL,
        status TEXT NOT NULL,sample_size INTEGER NOT NULL,payload TEXT NOT NULL,
        created_at TEXT NOT NULL,confirmed_at TEXT,UNIQUE(run_id,seq))""")
    # 每批至多一条“已确认”：SQLite 部分唯一索引兼容性差，改由 confirm() 在事务内保证。
    db.execute("CREATE INDEX IF NOT EXISTS idx_sampling_active ON sampling_versions(run_id,status)")
    db.execute("""CREATE TABLE IF NOT EXISTS sample_claims(
        id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,parcel TEXT NOT NULL,
        claimed_by TEXT NOT NULL,claimed_at TEXT NOT NULL,UNIQUE(run_id,parcel))""")
    return db

def _now(): return datetime.now(timezone.utc).isoformat()

def _merge_claims(payload,claims):
    claimed={p:by for p,by in claims}
    for sample in payload["samples"]:
        if sample["parcel"] in claimed: sample["claimed_by"]=claimed[sample["parcel"]]
    return payload

def _row_to_version(row,claims):
    payload=json.loads(row[6])
    return {"id":row[0],"run_id":row[1],"base_run_id":row[2],"seq":row[3],"status":row[4],
            "sample_size":row[5],"created_at":row[7],"confirmed_at":row[8],
            **_merge_claims(payload,claims)}

def _claims(db,run_id):
    return db.execute("SELECT parcel,claimed_by FROM sample_claims WHERE run_id=? ORDER BY id",(run_id,)).fetchall()

def list_versions(run_id):
    with connect() as db:
        rows=db.execute("SELECT * FROM sampling_versions WHERE run_id=? ORDER BY seq",(run_id,)).fetchall()
        claims=_claims(db,run_id)
    return [_row_to_version(r,claims) for r in rows]

def get_active(run_id):
    with connect() as db:
        row=db.execute("SELECT * FROM sampling_versions WHERE run_id=? AND status='已确认'",(run_id,)).fetchone()
        if not row: return None
        return _row_to_version(row,_claims(db,run_id))

def get_version(run_id,seq):
    with connect() as db:
        row=db.execute("SELECT * FROM sampling_versions WHERE run_id=? AND seq=?",(run_id,seq)).fetchone()
        if not row: raise DomainError("VERSION_NOT_FOUND","抽检版本不存在",404)
        return _row_to_version(row,_claims(db,run_id))

def _latest(db,run_id):
    return db.execute("SELECT * FROM sampling_versions WHERE run_id=? ORDER BY seq DESC LIMIT 1",(run_id,)).fetchone()

def _timeliness_rows(run_id):
    return store.get(run_id)["stages"][4]["rows"]

def modules_m5_overdue(run):
    """当前批次时效模块判定的超时包裹（抽检候选池）。"""
    return rules.overdue_pool(run["stages"][4]["rows"])

def create_or_refresh_draft(run_id,candidate_run_id,sample_size):
    """建立或重算待确认清单。同输入重试幂等；异常集合未变则不产生新版本。"""
    store.get(candidate_run_id)  # 候选批次必须存在
    rows=_timeliness_rows(candidate_run_id)
    with connect() as db:
        active_row=db.execute("SELECT * FROM sampling_versions WHERE run_id=? AND status='已确认'",(run_id,)).fetchone()
        latest_row=_latest(db,run_id)
        if active_row:
            active=_row_to_version(active_row,_claims(db,run_id))
            plan=rules.revise_draft(rows,sample_size,active["samples"])
            if not rules.changed(plan):
                return {"changed":False,"version":active}
        else:
            plan=rules.build_draft(rows,sample_size)
        after=plan["diff"]["after"]
        if latest_row and latest_row[4]=="待确认":
            latest_payload=json.loads(latest_row[6])
            if latest_row[5]==sample_size and latest_payload["diff"]["after"]==after:
                return {"changed":rules.changed(plan),"version":_row_to_version(latest_row,_claims(db,run_id))}
        seq=(latest_row[3]+1) if latest_row else 1
        vid="%s-v%d" % (run_id,seq)
        db.execute("INSERT INTO sampling_versions(id,run_id,base_run_id,seq,status,sample_size,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (vid,run_id,active_row[2] if active_row else run_id,seq,"待确认",sample_size,
                    json.dumps(plan,ensure_ascii=False),_now()))
        row=_latest(db,run_id)
        return {"changed":rules.changed(plan),"version":_row_to_version(row,_claims(db,run_id))}

def confirm(run_id,seq):
    with connect() as db:
        row=db.execute("SELECT * FROM sampling_versions WHERE run_id=? AND seq=?",(run_id,seq)).fetchone()
        if not row: raise DomainError("VERSION_NOT_FOUND","抽检版本不存在",404)
        if row[4]=="已确认": return _row_to_version(row,_claims(db,run_id))  # 幂等
        if row[4]!="待确认": raise DomainError("VERSION_NOT_DRAFT","该版本不可确认",409)
        db.execute("UPDATE sampling_versions SET status='已归档' WHERE run_id=? AND status='已确认'",(run_id,))
        db.execute("UPDATE sampling_versions SET status='已确认',confirmed_at=? WHERE id=?",(_now(),row[0]))
        fresh=db.execute("SELECT * FROM sampling_versions WHERE id=?",(row[0],)).fetchone()
        return _row_to_version(fresh,_claims(db,run_id))

def claim(run_id,parcel,claimed_by):
    if not claimed_by or not str(claimed_by).strip():
        raise DomainError("BAD_CLAIMER","请提供领取客服标识")
    claimed_by=str(claimed_by).strip()
    with connect() as db:
        active=db.execute("SELECT * FROM sampling_versions WHERE run_id=? AND status='已确认'",(run_id,)).fetchone()
        if not active: raise DomainError("NO_CONFIRMED_LIST","批次尚无已确认抽检清单，不能领取",409)
        payload=json.loads(active[6])
        if parcel not in [s["parcel"] for s in payload["samples"]]:
            raise DomainError("NOT_SAMPLED","包裹不在已确认抽检清单中",422)
        existing=db.execute("SELECT claimed_by FROM sample_claims WHERE run_id=? AND parcel=?",(run_id,parcel)).fetchone()
        if existing:
            if existing[0]!=claimed_by: raise DomainError("ALREADY_CLAIMED","该样本已被其他客服领取",409)
        else:
            try:
                db.execute("INSERT INTO sample_claims(run_id,parcel,claimed_by,claimed_at) VALUES(?,?,?,?)",
                           (run_id,parcel,claimed_by,_now()))
            except sqlite3.IntegrityError:  # 并发下同样本重复领取
                raise DomainError("ALREADY_CLAIMED","该样本已被其他客服领取",409)
        claims=_claims(db,run_id)
        return _row_to_version(active,claims)
