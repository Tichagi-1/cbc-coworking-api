"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-04-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


# ── Enum type definitions ──────────────────────────────────────────────────
user_role = sa.Enum("admin", "manager", "tenant", "owner", name="userrole")
unit_type = sa.Enum("office", "meeting_room", "hot_desk", "open_space", name="unittype")
unit_status = sa.Enum("vacant", "occupied", "reserved", name="unitstatus")
lease_status = sa.Enum("active", "expired", "terminated", "pending", name="leasestatus")
booking_payment_type = sa.Enum("coins", "money", name="bookingpaymenttype")
coin_tx_reason = sa.Enum(
    "monthly_accrual", "manual_admin", "booking_debit", "refund", name="cointxreason"
)


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind, checkfirst=True)
    unit_type.create(bind, checkfirst=True)
    unit_status.create(bind, checkfirst=True)
    lease_status.create(bind, checkfirst=True)
    booking_payment_type.create(bind, checkfirst=True)
    coin_tx_reason.create(bind, checkfirst=True)

    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="tenant"),
        sa.Column("telegram_id", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── buildings ──────────────────────────────────────────────────────────
    op.create_table(
        "buildings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("building_class", sa.String(length=10), nullable=False),
        sa.Column("total_area", sa.Float(), nullable=False),
        sa.Column("leasable_area", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── floors ─────────────────────────────────────────────────────────────
    op.create_table(
        "floors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("building_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("floor_plan_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["building_id"], ["buildings.id"], name="fk_floors_building_id"),
    )

    # ── units ──────────────────────────────────────────────────────────────
    op.create_table(
        "units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("floor_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("unit_type", unit_type, nullable=False),
        sa.Column("status", unit_status, nullable=False, server_default="vacant"),
        sa.Column("area_m2", sa.Float(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("monthly_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("photos", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["floor_id"], ["floors.id"], name="fk_units_floor_id"),
    )

    # ── zones ──────────────────────────────────────────────────────────────
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("floor_id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("points", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("zone_type", unit_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["floor_id"], ["floors.id"], name="fk_zones_floor_id"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_zones_unit_id"),
    )

    # ── tenants ────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
        sa.Column("plan_type", sa.String(length=100), nullable=True),
        sa.Column("monthly_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("coin_balance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_resident", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("zoho_contact_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_tenants_user_id"),
    )

    # ── leases ─────────────────────────────────────────────────────────────
    op.create_table(
        "leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=False),
        sa.Column("end_date", sa.DateTime(), nullable=False),
        sa.Column("monthly_rate", sa.Float(), nullable=False),
        sa.Column("status", lease_status, nullable=False, server_default="pending"),
        sa.Column("zoho_contract_id", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_leases_unit_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_leases_tenant_id"),
    )

    # ── meeting_rooms ──────────────────────────────────────────────────────
    op.create_table(
        "meeting_rooms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("rate_coins_per_hour", sa.Float(), nullable=False),
        sa.Column("rate_money_per_hour", sa.Float(), nullable=False),
        sa.Column("amenities", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_meeting_rooms_unit_id"),
        sa.UniqueConstraint("unit_id", name="uq_meeting_rooms_unit_id"),
    )

    # ── bookings ───────────────────────────────────────────────────────────
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("payment_type", booking_payment_type, nullable=False),
        sa.Column("coins_charged", sa.Float(), nullable=False, server_default="0"),
        sa.Column("money_charged", sa.Float(), nullable=False, server_default="0"),
        sa.Column("zoho_invoice_id", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="web"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["room_id"], ["meeting_rooms.id"], name="fk_bookings_room_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_bookings_tenant_id"),
    )

    # ── coin_transactions ──────────────────────────────────────────────────
    op.create_table(
        "coin_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column("reason", coin_tx_reason, nullable=False),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_coin_transactions_tenant_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("coin_transactions")
    op.drop_table("bookings")
    op.drop_table("meeting_rooms")
    op.drop_table("leases")
    op.drop_table("tenants")
    op.drop_table("zones")
    op.drop_table("units")
    op.drop_table("floors")
    op.drop_table("buildings")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    coin_tx_reason.drop(bind, checkfirst=True)
    booking_payment_type.drop(bind, checkfirst=True)
    lease_status.drop(bind, checkfirst=True)
    unit_status.drop(bind, checkfirst=True)
    unit_type.drop(bind, checkfirst=True)
    user_role.drop(bind, checkfirst=True)
