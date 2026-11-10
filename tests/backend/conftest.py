from dataclasses import dataclass
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.core.security import hash_password
from backend.app.db.bootstrap import initialize_policies
from backend.app.db.models import (
    AgentProfile,
    Base,
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
from backend.app.main import create_app

PASSWORD = "Tests-only-password-2026!"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_password_hash():
    return hash_password(PASSWORD)


@dataclass
class Harness:
    client: AsyncClient
    app: object
    settings: Settings
    users: dict
    tokens: dict

    def auth(self, login):
        return {"Authorization": f"Bearer {self.tokens[login]}"}

    async def create(self, key="new-ticket-0001", order="ST202611060001"):
        body = {"subject": "耳机故障需要协助", "body": "我的耳机没有声音，请帮忙排查问题"}
        if order:
            body["order_no"] = order
        result = await self.client.post(
            "/api/v1/tickets", json=body, headers={**self.auth("customer"), "Idempotency-Key": key}
        )
        assert result.status_code == 201, result.text
        return result.json()

    async def classify_assign(self, ticket):
        result = await self.client.post(
            f"/api/v1/tickets/{ticket['id']}/classification-review",
            json={
                "expected_version": ticket["version"],
                "category": 4,
                "reason": "人工确认质量诉求",
            },
            headers=self.auth("admin"),
        )
        assert result.status_code == 200, result.text
        classified = result.json()
        result = await self.client.post(
            f"/api/v1/tickets/{ticket['id']}/assign",
            json={"expected_version": classified["version"], "reason": "按技能与负载分配"},
            headers=self.auth("admin"),
        )
        assert result.status_code == 200, result.text
        return result.json()


@pytest.fixture
async def api(tmp_path, test_password_hash):
    # Only this newly created temporary SQLite file is touched. Not MySQL lock evidence.
    settings = Settings(
        env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        jwt_secret="test-only-random-key-0123456789-abcdefgh",
        worker_heartbeat_path=tmp_path / "worker.json",
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.sessions() as session:
        users = {}
        for login, role in (
            ("admin", "ADMIN"),
            ("customer", "CUSTOMER"),
            ("other", "CUSTOMER"),
            ("agent", "AGENT"),
            ("agent2", "AGENT"),
            ("restricted", "AGENT"),
        ):
            user = User(login=login, role=role, password_hash=test_password_hash)
            session.add(user)
            session.flush()
            users[login] = user.id
        initialize_policies(session)
        group = BusinessGroup(code="quality", name="质量技能组")
        session.add(group)
        session.flush()
        for login in ("agent", "agent2", "restricted"):
            session.add(AgentProfile(user_id=users[login], online=True, capacity=1))
            session.add(
                GroupMember(
                    group_id=group.id, user_id=users[login], can_view_business=login != "restricted"
                )
            )
        session.add(RoutingRule(category=4, group_id=group.id, rank=0))
        product = Product(sku="DEMO-EARPHONE", name="模拟蓝牙耳机")
        session.add(product)
        session.flush()
        for login, number in (("customer", "ST202611060001"), ("other", "ST202611060002")):
            order = Order(
                order_no=number,
                customer_id=users[login],
                status="SHIPPED",
                total_amount=Decimal("199.00"),
            )
            session.add(order)
            session.flush()
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    line_no=1,
                    product_name_snapshot=product.name,
                    quantity=1,
                    unit_price=Decimal("199.00"),
                )
            )
            session.add(
                Payment(
                    order_id=order.id,
                    record_no=f"demo-pay-{login}",
                    amount=Decimal("199.00"),
                    status="SUCCEEDED",
                    occurred_at=utcnow(),
                )
            )
            session.add(
                Shipment(
                    order_id=order.id,
                    tracking_no=f"DEMO-{login}",
                    carrier="模拟物流",
                    status="IN_TRANSIT",
                )
            )
            session.add(
                Refund(
                    order_id=order.id,
                    record_no=f"demo-refund-{login}",
                    amount=Decimal("199.00"),
                    status="PENDING",
                    reason="模拟售后申请",
                )
            )
        session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = {}
        for login in users:
            response = await client.post(
                "/api/v1/auth/login", json={"login": login, "password": PASSWORD}
            )
            assert response.status_code == 200, response.text
            tokens[login] = response.json()["access_token"]
        yield Harness(client, app, settings, users, tokens)
    app.state.database.dispose()
