# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 月末抽检（M8）

- `GET /api/sampling?run_id=..`：版本链与当前已确认（可执行）版本；不带 run_id 取最新批次。
- `POST /api/sampling/drafts`：`{run_id, candidate_run_id?, sample_size}`。按最新超时异常建立或重算“待确认”清单；补传导致异常集合变化才新增版本，同输入重试幂等。已确认清单不被覆盖。
- `POST /api/sampling/confirm`：`{run_id, seq}`。确认某草稿，提升为该批唯一可执行清单，旧已确认版本转“已归档”保留。
- `POST /api/sampling/claim`：`{run_id, parcel, claimed_by}`。仅可在已确认清单上领取；同客服重复领取幂等，他人抢占返回409，未确认清单领取返回409。
- 已领取样本补传后离开超时池：重抽返回409 `CLAIMED_LEFT_POOL`，需人工处置；归档仅含已确认清单的样本（kind=sample）。
