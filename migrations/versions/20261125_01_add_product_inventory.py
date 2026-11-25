"""Add product inventory for unique codes, links, and accounts.

Revision ID: 20261125_01
Revises: 20261123_01
"""

from alembic import op


revision = "20261125_01"
down_revision = "20261123_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_inventory (
            id BIGSERIAL PRIMARY KEY,
            product_id BIGINT NOT NULL REFERENCES products(id),
            item_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            order_id BIGINT REFERENCES orders(id),
            allocated_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT ''
        );
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'product_inventory_item_type_check'
            ) THEN
                ALTER TABLE product_inventory
                ADD CONSTRAINT product_inventory_item_type_check
                CHECK (item_type IN ('code', 'link', 'account'));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'product_inventory_status_check'
            ) THEN
                ALTER TABLE product_inventory
                ADD CONSTRAINT product_inventory_status_check
                CHECK (
                    status IN (
                        'available',
                        'allocated',
                        'delivered',
                        'removed'
                    )
                );
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'product_inventory_order_link_check'
            ) THEN
                ALTER TABLE product_inventory
                ADD CONSTRAINT product_inventory_order_link_check
                CHECK (
                    (
                        status IN ('available', 'removed')
                        AND order_id IS NULL
                    )
                    OR (
                        status IN ('allocated', 'delivered')
                        AND order_id IS NOT NULL
                    )
                );
            END IF;
        END $$;
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS product_inventory_product_status_idx
        ON product_inventory (product_id, status);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS product_inventory_available_idx
        ON product_inventory (product_id, id)
        WHERE status = 'available';
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS product_inventory_order_id_idx
        ON product_inventory (order_id)
        WHERE order_id IS NOT NULL;
    """)


def downgrade():
    op.execute("""
        DROP INDEX IF EXISTS product_inventory_order_id_idx;
    """)
    op.execute("""
        DROP INDEX IF EXISTS product_inventory_available_idx;
    """)
    op.execute("""
        DROP INDEX IF EXISTS product_inventory_product_status_idx;
    """)
    op.execute("""
        ALTER TABLE product_inventory
        DROP CONSTRAINT IF EXISTS product_inventory_order_link_check;
    """)
    op.execute("""
        ALTER TABLE product_inventory
        DROP CONSTRAINT IF EXISTS product_inventory_status_check;
    """)
    op.execute("""
        ALTER TABLE product_inventory
        DROP CONSTRAINT IF EXISTS product_inventory_item_type_check;
    """)
    op.execute("""
        DROP TABLE IF EXISTS product_inventory;
    """)
