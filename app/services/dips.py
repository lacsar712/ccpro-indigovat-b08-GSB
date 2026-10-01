"""浸染登记业务规则：同缸并发/重复提交只许一笔入库。"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DipLot, Vat


class DipSubmissionError(Exception):
    """登记失败（中文提示）。抛出时不得留下任何半插入数据。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class DuplicateDipSubmission(DipSubmissionError):
    """同一提交凭证已入库（连点/重复提交/并发竞争的落败方）。"""


def _parse_decimal(raw: str, label: str, *, require_positive: bool) -> Decimal:
    try:
        value = Decimal(raw)
        if not value.is_finite():
            raise InvalidOperation
        value = value.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise DipSubmissionError(f"{label}格式无效，请填写数字。")
    if require_positive:
        if value <= 0:
            raise DipSubmissionError("布料米数须大于 0。")
        if value > Decimal("99999999.99"):
            raise DipSubmissionError("布料米数超出允许范围。")
    elif value > Decimal("999999.99") or value < Decimal("-999999.99"):
        raise DipSubmissionError("氧化还原电位超出允许范围。")
    return value


def _parse_dipped_at(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        raise DipSubmissionError("浸染时间格式无效，请重新选择。")


def log_dip_lot(
    db: Session,
    *,
    vat_id: int,
    submit_token: str,
    dipped_at_raw: str,
    cloth_raw: str,
    redox_raw: str,
) -> Optional[DipLot]:
    """登记一笔浸染。

    幂等保证：同一 submit_token（连点/重复提交/并发）全库只入库一笔，
    落败方抛 DuplicateDipSubmission（中文失败），且无任何半插入。

    顺序：先在事务外解析全部入参（脏数据不产生写入）→ 锁该缸行
    （同缸并发串行、异缸互不阻塞）→ 锁内查凭证 → 插入提交；
    唯一索引为多进程/最终兜底。
    """
    token = (submit_token or "").strip()
    if not token:
        raise DipSubmissionError("提交凭证缺失，请刷新页面后重新登记。")

    # 全部解析/校验先于任何 DB 写入：失败即零写入，无需回滚半截数据
    dipped_at = _parse_dipped_at(dipped_at_raw.strip())
    cloth = _parse_decimal(cloth_raw.strip(), "布料米数", require_positive=True)
    redox = (
        _parse_decimal(redox_raw.strip(), "氧化还原电位", require_positive=False)
        if redox_raw.strip()
        else None
    )

    # 锁住目标缸行：同缸的并发登记在此串行，不同缸各锁各行互不阻塞
    locked = db.query(Vat.id).filter(Vat.id == vat_id).with_for_update().first()
    if locked is None:
        raise DipSubmissionError("染缸不存在，无法登记浸染。")

    # 行锁内先查凭证：正常的连点/重复提交在此被拦下，根本不发 INSERT
    if db.query(DipLot.id).filter(DipLot.submit_token == token).first() is not None:
        db.rollback()
        raise DuplicateDipSubmission("该浸染已入库一笔，重复提交未重复记账，请勿连点。")

    lot = DipLot(
        vat_id=vat_id,
        dippedAt=dipped_at,
        clothMeters=cloth,
        redoxMv=redox,
        submit_token=token,
    )
    db.add(lot)
    try:
        db.commit()
    except IntegrityError:
        # 唯一索引兜底：跨进程/绕过行锁的极端竞争下，落败事务整笔回滚
        db.rollback()
        raise DuplicateDipSubmission("该浸染已入库一笔，重复提交未重复记账，请勿连点。")
    return lot
