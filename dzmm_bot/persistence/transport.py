"""Existing transport schema, preserved by migrations."""
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, UniqueConstraint

meta = MetaData()
rooms = Table("group_chats", meta, Column("id", String(200), primary_key=True),
              Column("kind", String(20), nullable=False), Column("enabled", Integer, nullable=False))
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
               Column("reply_to_text", Text),
               Column("text", Text, nullable=False), Column("status", String(20), nullable=False),
               Column("created", Float, nullable=False), Column("available", Float, nullable=False),
               Column("lease", String(36)), Column("lease_until", Float),
               Column("attempts", Integer, nullable=False), Column("platform_id", String(200)),
               Column("error", String(100)))

