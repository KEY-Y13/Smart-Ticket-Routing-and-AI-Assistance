import pytest
from sqlalchemy import func, select

from backend.app.db.models import Order, User
from backend.app.db.simulation import seed_simulation


@pytest.mark.anyio
async def test_explicit_simulation_has_owned_orders_and_refuses_second_import(api):
    with api.app.state.database.sessions() as session:
        summary = seed_simulation(session, "Tests-only-demo-password-2026!")
        session.commit()
        assert summary["source"] == "SIMULATED" and summary["orders"] == 2
        owner = session.scalar(select(User).where(User.login == "demo_customer_1"))
        order = session.scalar(select(Order).where(Order.order_no == "ST202611060101"))
        assert order.customer_id == owner.id
        total = session.scalar(select(func.count()).select_from(User))
        with pytest.raises(ValueError, match="拒绝覆盖"):
            seed_simulation(session, "Tests-only-demo-password-2026!")
        session.rollback()
        assert session.scalar(select(func.count()).select_from(User)) == total
