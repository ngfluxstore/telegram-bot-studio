
"""Add automatic and manual product delivery.

Revision ID: 20261123_01
Revises: 20260629_01
"""

from alembic import op


revision = "20261123_01"
down_revision = "20260629_01"
branch_labels = None
depends_on = None


def upgrade():
    # Product delivery configuration
    op.execute("""
        ALTER TABLE products
        ADD COLUMN IF NOT EXISTS delivery_mode
        TEXT NOT NULL DEFAULT 'manual';
    """)

    op.execute("""
        ALTER TABLE products
        ADD COLUMN IF NOT EXISTS delivery_content
        TEXT NOT NULL DEFAULT '';
    """)

    # Order delivery snapshot and tracking
    op.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS delivery_mode
        TEXT NOT NULL DEFAULT 'manual';
    """)

    op.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS delivery_content
        TEXT NOT NULL DEFAULT '';
    """)

    op.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS delivery_status
        TEXT NOT NULL DEFAULT 'pending';
    """)

    op.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS delivered_at
        TIMESTAMPTZ;
    """)

    # Add constraints safely
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'products_delivery_mode_check'
            ) THEN
                ALTER TABLE products
                ADD CONSTRAINT products_delivery_mode_check
                CHECK (delivery_mode IN ('automatic', 'manual'));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'orders_delivery_mode_check'
            ) THEN
                ALTER TABLE orders
                ADD CONSTRAINT orders_delivery_mode_check
                CHECK (delivery_mode IN ('automatic', 'manual'));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'orders_delivery_status_check'
            ) THEN
                ALTER TABLE orders
                ADD CONSTRAINT orders_delivery_status_check
                CHECK (
                    delivery_status IN (
                        'pending',
                        'processing',
                        'delivered',
                        'failed'
                    )
                );
            END IF;
        END $$;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE orders
        DROP CONSTRAINT IF EXISTS orders_delivery_status_check;
    """)

    op.execute("""
        ALTER TABLE orders
        DROP CONSTRAINT IF EXISTS orders_delivery_mode_check;
    """)

    op.execute("""
        ALTER TABLE products
        DROP CONSTRAINT IF EXISTS products_delivery_mode_check;
    """)

    op.execute("""
        ALTER TABLE orders
        DROP COLUMN IF EXISTS delivered_at,
        DROP COLUMN IF EXISTS delivery_status,
        DROP COLUMN IF EXISTS delivery_content,
        DROP COLUMN IF EXISTS delivery_mode;
    """)

    op.execute("""
        ALTER TABLE products
        DROP COLUMN IF EXISTS delivery_content,
        DROP COLUMN IF EXISTS delivery_mode;
    """)
