from sqlalchemy import select

from backend.app.core.errors import AppError
from backend.app.core.security import hash_password
from backend.app.db.models import (
    AdminEvent,
    AgentProfile,
    BusinessGroup,
    GroupMember,
    RoutingRule,
    User,
)
from backend.app.domain.tickets import Role
from backend.app.schemas.business import UserView
from backend.app.services.tickets import check_version, lock_dispatch, require_role


def audit(session, user, action, target_type, target_id, detail, request_id):
    session.add(
        AdminEvent(
            actor_id=user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            request_id=request_id,
        )
    )


def create_user(session, user, body, request_id):
    require_role(user, Role.ADMIN)
    if session.scalar(select(User.id).where(User.login == body.login)):
        raise AppError(409, "LOGIN_EXISTS", "登录名已存在")
    created = User(
        login=body.login,
        password_hash=hash_password(body.password.get_secret_value()),
        role=body.role,
    )
    session.add(created)
    session.flush()
    if body.role == Role.AGENT:
        session.add(AgentProfile(user_id=created.id))
    audit(session, user, "USER_CREATED", "user", created.id, {"role": body.role}, request_id)
    session.commit()
    return UserView.model_validate(created)


def patch_user(session, user, target_id, body, request_id):
    require_role(user, Role.ADMIN)
    lock_dispatch(session)
    target = session.scalar(select(User).where(User.id == target_id).with_for_update())
    if target is None:
        raise AppError(404, "USER_NOT_FOUND", "用户不存在")
    check_version(target, body.expected_version)
    if target.id == user.id and (body.enabled is False or body.role not in (None, Role.ADMIN)):
        raise AppError(409, "SELF_LOCKOUT_FORBIDDEN", "不能在当前会话停用或降级自己")
    changing_access = (body.enabled is not None and body.enabled != target.enabled) or (
        body.role is not None and body.role != target.role
    )
    profile = session.get(AgentProfile, target.id)
    if changing_access and profile and profile.active_load:
        raise AppError(409, "ACTIVE_ASSIGNMENTS_EXIST", "请先将该客服的活跃工单重新分配")
    if body.enabled is not None:
        target.enabled = body.enabled
    if body.role is not None:
        target.role = body.role
        if target.role == Role.AGENT and profile is None:
            session.add(AgentProfile(user_id=target.id))
        if target.role != Role.AGENT and profile:
            profile.online = False
    if changing_access:
        target.auth_version += 1
    audit(
        session,
        user,
        "USER_UPDATED",
        "user",
        target.id,
        {"role": target.role, "enabled": target.enabled},
        request_id,
    )
    session.commit()
    return UserView.model_validate(target)


def create_group(session, user, body, request_id):
    require_role(user, Role.ADMIN)
    group = BusinessGroup(code=body.code, name=body.name)
    session.add(group)
    session.flush()
    audit(session, user, "GROUP_CREATED", "group", group.id, {"code": body.code}, request_id)
    session.commit()
    return {"id": str(group.id), "code": group.code, "name": group.name}


def add_member(session, user, group_id, body, request_id):
    require_role(user, Role.ADMIN)
    lock_dispatch(session)
    group, target = session.get(BusinessGroup, group_id), session.get(User, body.user_id)
    if group is None or target is None:
        raise AppError(404, "RESOURCE_NOT_FOUND", "业务组或用户不存在")
    if target.role != Role.AGENT:
        raise AppError(409, "NOT_AN_AGENT", "仅客服可加入技能组")
    if session.get(GroupMember, (group_id, body.user_id)):
        raise AppError(409, "MEMBER_EXISTS", "成员已存在")
    session.add(GroupMember(group_id=group_id, **body.model_dump()))
    audit(
        session,
        user,
        "MEMBER_ADDED",
        "group",
        group_id,
        {"user_id": target.id, "can_view_business": body.can_view_business},
        request_id,
    )
    session.commit()
    return {"group_id": str(group_id), "user_id": str(target.id)}


def create_rule(session, user, body, request_id):
    require_role(user, Role.ADMIN)
    lock_dispatch(session)
    if session.get(BusinessGroup, body.group_id) is None:
        raise AppError(404, "GROUP_NOT_FOUND", "业务组不存在")
    rule = RoutingRule(**body.model_dump())
    session.add(rule)
    session.flush()
    audit(
        session,
        user,
        "ROUTING_RULE_CREATED",
        "routing_rule",
        rule.id,
        {"category": body.category, "group_id": body.group_id},
        request_id,
    )
    session.commit()
    return {"id": str(rule.id), "category": rule.category, "group_id": str(rule.group_id)}


def update_agent(session, user, target_id, body, request_id):
    require_role(user, Role.AGENT, Role.ADMIN)
    if user.role != Role.ADMIN and target_id != user.id:
        raise AppError(403, "FORBIDDEN", "只能修改自己的在线状态")
    if body.capacity is not None and user.role != Role.ADMIN:
        raise AppError(403, "CAPACITY_FORBIDDEN", "仅管理员可修改容量")
    lock_dispatch(session)
    target = session.get(User, target_id)
    profile = session.get(AgentProfile, target_id)
    if target is None or target.role != Role.AGENT or profile is None:
        raise AppError(404, "AGENT_NOT_FOUND", "客服不存在")
    check_version(profile, body.expected_version)
    if body.online and not target.enabled:
        raise AppError(409, "AGENT_DISABLED", "已禁用客服不能上线")
    if body.capacity is not None:
        if body.capacity < profile.active_load:
            raise AppError(409, "CAPACITY_BELOW_LOAD", "容量不能低于当前活跃负载")
        profile.capacity = body.capacity
    profile.online = body.online
    audit(
        session,
        user,
        "AGENT_UPDATED",
        "agent",
        target_id,
        {"online": profile.online, "capacity": profile.capacity},
        request_id,
    )
    session.commit()
    return {
        "user_id": str(target_id),
        "online": profile.online,
        "capacity": profile.capacity,
        "active_load": profile.active_load,
        "version": profile.version,
    }
