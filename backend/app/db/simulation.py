"""Explicit fictional business fixtures for a newly provisioned project database."""

from decimal import Decimal

from sqlalchemy import select

from backend.app.core.security import hash_password
from backend.app.db.models import (
    AdminEvent,
    AgentProfile,
    BusinessGroup,
    GroupMember,
    Order,
    OrderItem,
    Payment,
    Product,
    Refund,
    RoutingRule,
    Shipment,
    User,
    utcnow,
)


def seed_simulation(session, password: str) -> dict:
    if len(password) < 12:
        raise ValueError("模拟账户密码至少 12 字符，不使用写死的默认密码")
    administrator = session.scalar(select(User).where(User.role == "ADMIN", User.enabled.is_(True)))
    if administrator is None:
        raise ValueError("请先显式创建首个管理员")
    if session.scalar(select(User.id).where(User.login.startswith("demo_", autoescape=True))):
        raise ValueError("已有 demo_ 用户，拒绝覆盖或重置模拟数据")
    users = {}
    for name, role in (
        ("demo_customer_1", "CUSTOMER"),
        ("demo_customer_2", "CUSTOMER"),
        ("demo_agent_1", "AGENT"),
        ("demo_agent_2", "AGENT"),
    ):
        user = User(login=name, role=role, password_hash=hash_password(password))
        session.add(user)
        session.flush()
        users[name] = user
    group = BusinessGroup(code="demo-general", name="模拟综合售后组")
    session.add(group)
    session.flush()
    for category in range(6):
        session.add(RoutingRule(group_id=group.id, category=category))
    for name in ("demo_agent_1", "demo_agent_2"):
        session.add(AgentProfile(user_id=users[name].id, online=True, capacity=2))
        session.add(
            GroupMember(
                group_id=group.id, user_id=users[name].id, can_view_business=name == "demo_agent_1"
            )
        )
    for number, product_name, amount in (
        (1, "模拟蓝牙耳机", "199.00"),
        (2, "模拟路由器", "299.00"),
    ):
        product = Product(sku=f"DEMO-SKU-{number}", name=product_name)
        session.add(product)
        session.flush()
        order = Order(
            order_no=f"ST20261106010{number}",
            customer_id=users[f"demo_customer_{number}"].id,
            status="SHIPPED",
            total_amount=Decimal(amount),
        )
        session.add(order)
        session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                line_no=1,
                quantity=1,
                product_name_snapshot=product_name,
                unit_price=Decimal(amount),
            )
        )
        session.add(
            Payment(
                order_id=order.id,
                record_no=f"DEMO-PAY-{number}",
                amount=Decimal(amount),
                status="SUCCEEDED",
                occurred_at=utcnow(),
            )
        )
        session.add(
            Shipment(
                order_id=order.id,
                tracking_no=f"DEMO-TRACK-{number}",
                carrier="虚构物流商",
                status="IN_TRANSIT",
            )
        )
        if number == 1:
            session.add(
                Refund(
                    order_id=order.id,
                    record_no="DEMO-REFUND-1",
                    amount=Decimal(amount),
                    status="PENDING",
                    reason="模拟申请，非真实退款",
                )
            )
    summary = {
        "source": "SIMULATED",
        "users": 4,
        "products": 2,
        "orders": 2,
        "payments": 2,
        "shipments": 2,
        "refunds": 1,
        "business_groups": 1,
    }
    session.add(
        AdminEvent(
            actor_id=administrator.id,
            action="SIMULATION_IMPORTED",
            target_type="dataset",
            target_id=0,
            detail=summary,
            request_id="explicit-simulation-bootstrap",
        )
    )
    session.flush()
    return summary
