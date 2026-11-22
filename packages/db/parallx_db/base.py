"""Declarative base and shared column conventions.

The naming convention matters more than it looks: without it, Alembic
autogenerate produces migrations whose constraint names differ from the ones
Postgres invented, and a later `drop constraint` fails on a database that was
created by a different route. Naming them explicitly makes migrations
reproducible.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix":  "ix_%(table_name)s_%(column_0_N_name)s",
    "uq":  "uq_%(table_name)s_%(column_0_N_name)s",
    "ck":  "ck_%(table_name)s_%(constraint_name)s",
    "fk":  "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk":  "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Reusable column types, so "a ticker" means the same width everywhere.
str16 = Annotated[str, mapped_column(String(16))]
str64 = Annotated[str, mapped_column(String(64))]
str255 = Annotated[str, mapped_column(String(255))]

created_at = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False),
]
updated_at = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now(),
                  onupdate=func.now(), nullable=False),
]
