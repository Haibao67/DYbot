# Update and player information workflow

This system stores update drafts, broadcasts, feature tips, and world events in the existing database. Group broadcasts are queued as ordinary outbound messages and sent by the existing Worker. Do not send announcements through a separate HTTP loop.

## Configure update administrators

Set `DZMM_GAME_ADMINS` to a comma-separated list of platform sender IDs, then restart the service. With the setting empty, no chat user can run update or announcement administration commands. Keep the sender IDs out of manifests and source control where possible.

## Add and publish a version

1. Copy `docs/update-manifest-template.json` to `dzmm_bot/domain/updates/<version>.json`, for example `1.6.0.json`.
2. Replace the example values. `version`, `title`, `theme`, `summary`, `features`, `commands`, `tips`, and `changelog` are required. `published_date` is optional and, when set, appears in the stable broadcast body. Every feature needs `id`, `name`, `short_description`, and `details`. Use only player-safe prose in the announcement fields; `changelog` is shown only to configured administrators.
3. In a group where the bot is enabled, an update administrator runs `/创建更新 1.6.0`, then `/预览更新 1.6.0`. Preview uses the same renderer as the eventual broadcast.
4. After reviewing the preview, run `/发布更新 1.6.0`. This transaction creates one delivery per enabled group and queues them for the Worker. Publishing an already-published update is rejected.
5. Check `/公告状态 1.6.0`. Failed deliveries can be listed with `/公告失败 1.6.0` and requeued with `/重试公告 1.6.0`; successful rooms are not requeued.

Updates are version-unique. Never edit a manifest for a version that has already been published; create a new version for corrections. `/更新` shows the latest or a specific published version, `/更新日志` shows player-visible history, and `/更新日志 <version>` includes the technical changelog only for configured administrators.

## Player help and operations

- `/怎么玩` shows currently available feature entries. `/怎么玩 <feature id or name>` shows a short guide.
- An onboarding tip is configured on a feature using `trigger_commands` and `onboarding_tip`; each player-feature pair is recorded once.
- `/今日` aggregates currently available market information and a recent update. It does not fabricate weather or inactive game modules.
- `/待办` aggregates registered providers. Add future module providers through `TodoService.register_provider_type(Provider)`; providers receive `(db, player_id, now)` and implement `items()`.
- A configured game administrator sends `/公告 <text>` to the Bot in a private chat to queue a global announcement to every enabled group. Ordinary private text is never broadcast. The Bot privately replies to the command with the target count. If the formatted body exceeds the single-message limit (1000 UTF-16 code units or 10 newlines), it creates no broadcast and reports the measured count and overflow in the private reply. The same command in a group does not broadcast. Do not place secrets, internal paths, or private player data in a public notice.
- Large harvests and factory critical results can create cooldown-limited world events. Their default scope is the source room; callers may explicitly request `global`.

World events remain disabled until `DZMM_WORLD_HARVEST_THRESHOLD`, `DZMM_WORLD_EVENT_PLAYER_COOLDOWN`, and `DZMM_WORLD_EVENT_GLOBAL_COOLDOWN` are configured with product-approved values. No example threshold or cooldown is assumed as live policy.

## Operations and recovery

Apply database changes with the existing Alembic migration path; migration `0009_player_information` adds information-system tables without resetting player data. Outbound delivery state follows the existing outbox and Worker retry policy. Tasks that become `uncertain` after a Worker interruption require the existing administrator resolution flow; resolving one also updates broadcast delivery/job counters. Never manually mark a delivery successful without verifying the platform result.

Use isolated fake rooms and a fake or simulated Worker for validation. Do not test a broadcast by sending to real player groups unless separately authorized.
