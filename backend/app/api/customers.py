from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import CustomerHistoryItem, CustomerHistoryRead
from ..services.ticket_service import get_customer_history
from .deps import get_current_user

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("/{customer_id}/history", response_model=CustomerHistoryRead)
def customer_history(customer_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    customer, total, tickets = get_customer_history(db, customer_id, current_user)
    return CustomerHistoryRead(
        customer_id=customer.id,
        total_tickets=total,
        recent_tickets=[
            CustomerHistoryItem(
                ticket_id=t.id,
                ticket_no=t.ticket_no,
                title=t.title,
                status=t.status,
                priority=t.priority,
                updated_at=t.updated_at,
            )
            for t in tickets
        ],
    )
