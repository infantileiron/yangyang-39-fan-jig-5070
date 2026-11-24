from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.constants.enums import CorrectionStatus
from src.domain.corrections import correction_idem_key, snapshot_correction, validate_correction
from src.models.base import utc_now
from src.models.workflow import AuditEvent, Correction, Observation
from src.services.errors import BusinessError
from src.utils.keys import content_key


def create_correction(db, payload, actor):
    try:
        trial, basis, corrected = validate_correction(payload)
    except ValueError as exc:
        raise BusinessError(str(exc)) from exc

    ledger = list(db.scalars(select(Observation).where(Observation.payload['trial'].as_string() == trial)))
    if not ledger:
        raise BusinessError(f'台账中没有试片 {trial} 的完工记录，无法登记更正', 404)
    # 原读数只含翘边/耗电，风档与夹具取台账同试片记录，拼成完整行再比对
    first_payload = ledger[0].payload
    full_basis = {'trial': trial, 'speed': first_payload['speed'], 'jig': first_payload['jig'],
                  **{key: basis[key] for key in ('warp', 'energy') if key in basis}}
    basis_key = content_key(full_basis)
    if not any(content_key(item.payload) == basis_key for item in ledger):
        raise BusinessError('原读数与台账记录对不上，请核对试片号和补抄时的原始读数后重试')

    idem_key = correction_idem_key(trial, basis)
    existing = db.scalar(select(Correction).where(Correction.idem_key == idem_key))
    if existing is not None:
        # 幂等重试：不新增记录、不写审计，直接返回同一条
        return existing, False

    locked = db.scalar(select(Correction).where(
        Correction.trial == trial, Correction.status == CorrectionStatus.VERIFIED))
    if locked is not None:
        raise BusinessError(
            f'试片 {trial} 的更正已由 {locked.verified_by} 核实锁定，晚到的旧记录不能冲掉已核实更正', 409)

    row = Correction(idem_key=idem_key, trial=trial, basis=basis, corrected=corrected,
                     basis_key=basis_key, status=CorrectionStatus.PENDING, created_by=actor)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise BusinessError('相同更正刚刚写入，请刷新后重试', 409) from exc
    db.add(AuditEvent(actor=actor, action='correction.created',
                      detail=f'试片 {trial} 待核实更正 #{row.id}'))
    return row, True


def verify_correction(db, correction_id, actor, note):
    row = db.get(Correction, correction_id, with_for_update=True)
    if row is None:
        raise BusinessError('更正记录不存在', 404)
    if row.status == CorrectionStatus.VERIFIED:
        raise BusinessError('这条更正已核实，无需重复操作', 409)
    if row.status == CorrectionStatus.SUPERSEDED:
        raise BusinessError('这条更正已被其它已核实更正替代', 409)

    # 同一试片只保留一条已核实更正；核实后其余待核实记录标记为被替代，晚到旧记录不能冲掉它
    others = list(db.scalars(select(Correction).where(
        Correction.trial == row.trial, Correction.id != row.id,
        Correction.status == CorrectionStatus.PENDING)))
    for other in others:
        other.status = CorrectionStatus.SUPERSEDED
    row.status = CorrectionStatus.VERIFIED
    row.verified_by = actor
    row.verified_at = utc_now()
    row.note = (note or '').strip() or None
    db.add(AuditEvent(actor=actor, action='correction.verified',
                      detail=f'试片 {row.trial} 更正 #{row.id} 核实，{len(others)} 条待核实记录被替代'))
    db.flush()
    return row


def list_corrections(db, trial=None):
    query = select(Correction).order_by(Correction.id.desc()).limit(200)
    if trial:
        query = select(Correction).where(Correction.trial == trial).order_by(Correction.id.desc()).limit(200)
    return list(db.scalars(query))


def current_snapshot(db):
    """排队时刻冻结的全部更正快照。"""
    return [snapshot_correction(item)
            for item in db.scalars(select(Correction).order_by(Correction.id))]
