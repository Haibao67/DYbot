"""Shared invitation approval for HTTP and game-chat administrators."""
import json
import uuid

from sqlalchemy import select

from dzmm_bot.persistence.schema import audit, pending_group_invites
from dzmm_bot.presentation.formatters import name_escape


def approve_invitation(db, now, actor, invite_id):
    invitation = db.execute(select(pending_group_invites).where(
        pending_group_invites.c.id == invite_id).with_for_update()).mappings().first()
    if not invitation:
        return None
    if invitation["status"] != "pending":
        return invitation["status"]
    db.execute(pending_group_invites.update().where(
        pending_group_invites.c.id == invite_id).values(status="approved", approved_at=now))
    db.execute(audit.insert().values(id=str(uuid.uuid4()), actor=actor,
        action="approve_group_invite", target_type="group_invite", target_id=invite_id,
        payload_json=json.dumps({}), created_at=now))
    return "approved"


class InviteApprovalService:
    def __init__(self, db, now, actor):
        self.db, self.now, self.actor = db, now, actor

    def pending(self):
        rows = self.db.execute(select(pending_group_invites).where(
            pending_group_invites.c.status == "pending").order_by(
            pending_group_invites.c.created_at).limit(5)).mappings().all()
        if not rows:
            return "📨 当前没有待处理的群聊邀请。"
        lines = ["📨 待处理群聊邀请"]
        lines.extend(f"{name_escape(row['group_name'])}｜{row['id']}" for row in rows)
        lines.append("/同意邀请 <邀请ID>")
        return "\n".join(lines)

    def approve(self, invite_id):
        status = approve_invitation(self.db, self.now, self.actor, invite_id)
        if status is None:
            return "⚠️ 未找到该邀请。"
        if status == "approved":
            return "✅ 已同意邀请，Bot 将尝试加入群聊，并在成功后私聊通知邀请者。"
        return f"ℹ️ 该邀请当前状态：{status}；不会重复入群。"
