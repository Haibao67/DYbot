"""Existing transport schema, preserved by migrations."""
from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, Text, UniqueConstraint

meta = MetaData()
rooms = Table("group_chats", meta, Column("id", String(200), primary_key=True),
              Column("kind", String(20), nullable=False), Column("enabled", Integer, nullable=False),
              Column("last_dispatched_at", Float))
players = Table("players", meta, Column("id", String(200), primary_key=True),
                Column("name", String(100), nullable=False), Column("created", Float, nullable=False))
members = Table("memberships", meta, Column("room", String(200), primary_key=True),
                Column("player", String(200), primary_key=True), Column("joined", Float, nullable=False))
inbox = Table("inbound_messages", meta, Column("id", String(36), primary_key=True),
              Column("room", String(200), nullable=False), Column("message_id", String(200), nullable=False),
              Column("sender", String(200), nullable=False), Column("text", Text, nullable=False),
              Column("created", Float, nullable=False), UniqueConstraint("room", "message_id"))
outbox = Table("outbound_messages", meta, Column("id", String(36), primary_key=True),
               Column("room", String(200), nullable=False), Column("kind", String(20), nullable=False),
               Column("reply_to_message_id", String(200)), Column("reply_to_sender_id", String(200)),
               Column("reply_to_text", Text), Column("reply_to_content_json", Text),
               Column("text", Text, nullable=False), Column("status", String(20), nullable=False),
               Column("created", Float, nullable=False), Column("available", Float, nullable=False),
               Column("last_claimed_at", Float), Column("last_send_started_at", Float),
               Column("last_result_at", Float),
               Column("lease", String(36)), Column("lease_until", Float),
               Column("attempts", Integer, nullable=False), Column("platform_id", String(200)),
               Column("error", String(100)), Column("error_detail", Text))
Index("ix_outbound_status_created", outbox.c.status, outbox.c.created)
Index("ix_outbound_last_result", outbox.c.last_result_at)
Index("ix_outbound_status_kind_reply", outbox.c.status, outbox.c.kind, outbox.c.reply_to_message_id)
Index("ix_outbound_room_status_created_id", outbox.c.room, outbox.c.status, outbox.c.created, outbox.c.id)

