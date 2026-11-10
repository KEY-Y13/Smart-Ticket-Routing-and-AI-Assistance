from sqlalchemy import select

from backend.app.core.errors import AppError
from backend.app.db.models import (
    BusinessGroup,
    GroupMember,
    Order,
    OrderItem,
    Payment,
    Refund,
    Shipment,
    TicketAssignment,
    utcnow,
)
from backend.app.domain.tickets import Role
from backend.app.services.tickets import event, get_ticket


def business_context(session, user, ticket_id, request_id, *, commit=True) -> dict:
    ticket = get_ticket(session, user, ticket_id)
    if user.role == Role.AGENT:
        assignment = session.scalar(
            select(TicketAssignment).where(
                TicketAssignment.ticket_id == ticket.id,
                TicketAssignment.ended_at.is_(None),
                TicketAssignment.agent_id == user.id,
            )
        )
        member = session.get(GroupMember, (assignment.group_id, user.id)) if assignment else None
        group = session.get(BusinessGroup, assignment.group_id) if assignment else None
        if member is None or not member.can_view_business or group is None or not group.enabled:
            raise AppError(403, "BUSINESS_SCOPE_FORBIDDEN", "当前业务组未授权查询业务记录")
    if ticket.order_id is None:
        raise AppError(404, "ORDER_NOT_LINKED", "当前工单尚未关联已确认归属的订单")
    order = session.scalar(
        select(Order).where(Order.id == ticket.order_id, Order.customer_id == ticket.customer_id)
    )
    if order is None:
        raise AppError(404, "ORDER_NOT_FOUND", "关联订单不存在或归属不匹配")
    event(
        session,
        ticket,
        user,
        "BUSINESS_CONTEXT_READ",
        "查询授权模拟业务记录",
        request_id,
        new={"order_id": order.id},
    )
    result = {
        "source": "SIMULATED_BUSINESS_DATABASE",
        "queried_at": utcnow(),
        "order": {
            "id": str(order.id),
            "order_no": order.order_no,
            "status": order.status,
            "total_amount": str(order.total_amount),
        },
        "items": [
            {
                "product_name": item.product_name_snapshot,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
            }
            for item in session.scalars(
                select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.line_no)
            )
        ],
        "payments": [
            {"status": row.status, "amount": str(row.amount), "occurred_at": row.occurred_at}
            for row in session.scalars(
                select(Payment).where(Payment.order_id == order.id).order_by(Payment.id)
            )
        ],
        "shipments": [
            {"carrier": row.carrier, "tracking_no": row.tracking_no, "status": row.status}
            for row in session.scalars(
                select(Shipment).where(Shipment.order_id == order.id).order_by(Shipment.id)
            )
        ],
        "refunds": [
            {"status": row.status, "amount": str(row.amount)}
            for row in session.scalars(
                select(Refund).where(Refund.order_id == order.id).order_by(Refund.id)
            )
        ],
    }
    if commit:
        session.commit()
    return result
