# Group invitation approval

The Worker watches one-on-one chats returned by DZMM `chat.listAll`. A message containing a valid `https://www.dzmm.io/invite/<code>` link, or the observed `share` / `group_invite` card with `resourceId=<code>`, becomes a pending invitation after `groupChat.getInviteInfo` confirms it. The Bot replies to that original private message using the original text or share-card content: “已记录群聊邀请，等待管理员同意。” An invitation sent in a group does not enter this flow.

After deploying migration `0010_group_invites`, review pending invitations through the existing administrator token:

```powershell
.\.venv\Scripts\python.exe -m dzmm_bot.manage invites
```

Check the invitation ID, group name, inviter and status. To authorize joining:

```powershell
.\.venv\Scripts\python.exe -m dzmm_bot.manage approve-invite <invitation-id>
```

Approval changes the status to `approved`. The connected Worker calls DZMM `groupChat.joinByInvite` once; only a response containing a group chatroom ID marks the invitation `joined` and enables the group in `group_chats`. In the same transaction, Core queues a private confirmation that quotes the original invitation message: “✅ 已接受邀请，现已加入「群名」。” The existing Core room sync then subscribes to messages in the group. Repeating approval never starts another join or sends another confirmation.

Game-chat administrators can also approve through the Bot's private chat: `/邀请列表` displays pending invitation IDs, and `/同意邀请 <邀请ID>` applies the same approval transition and audit record as the HTTP endpoint. These commands do not call the platform join API directly; the existing Worker performs that step. They require a static game-admin ID or a successful private `/管理员登陆` grant. This chat flow is local code until the next deployment.

If the status remains `joining` or becomes `failed`, inspect the real account's group membership before taking any manual action. A timeout can mean the server accepted the join but the response was lost. The Worker does not retry an uncertain join automatically. No production invitation is accepted merely by reading the link or by running local tests.

An authorized inspection of the existing Bot private chat on 2026-09-27 confirmed the invitation is a `content` object with `type: share`, `shareType: group_invite`, and `resourceId` containing the invitation code. Existing messages require an explicit history check because Socket `message:new` only delivers new messages. The invitation `C66OxXko` was approved, DZMM returned a joined room ID, and both private replies received successful send ACKs with the original share-card reference. A newly arriving Socket card event and the private chat UI rendering still need direct observation.
