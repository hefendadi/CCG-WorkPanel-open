"""Small write contracts for Ordering V1; persistence orchestration is out of scope."""

from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

from webapp.ordering.models import FinalOrderStatus


def confirmation_after_quantity_change(
    *,
    current_qty: Decimal,
    new_qty: Decimal,
    status: FinalOrderStatus,
    confirmed_by: Optional[str],
    confirmed_at: Optional[datetime],
) -> Tuple[FinalOrderStatus, Optional[str], Optional[datetime]]:
    """A changed confirmed quantity must return to an unconfirmed state."""
    if new_qty != current_qty and status == FinalOrderStatus.CONFIRMED:
        return FinalOrderStatus.UNCONFIRMED, None, None
    return status, confirmed_by, confirmed_at
