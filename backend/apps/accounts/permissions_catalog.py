"""Canonical permission codes and default role grants.

Roles and permissions are stored in the database (Role / Permission tables) and
seeded from this catalogue by the ``seed_roles`` migration so HQ can adjust
grants later without code changes.
"""

PERMISSIONS = {
    # Organisation
    "org.manage_franchisees": "Create and manage franchisees",
    "org.manage_stores": "Create stores and assign them to franchisees",
    "org.configure_store": "Configure store settings, hours, channels",
    "org.view_network": "View all franchisees and stores",
    "org.manage_devices": "Register and revoke POS/KDS devices",
    # People
    "people.manage_hq_staff": "Manage headquarters employees",
    "people.manage_franchise_staff": "Manage franchise-level users",
    "people.manage_store_staff": "Manage store employees",
    # Catalogue
    "catalog.manage_master": "Manage master product catalogue",
    "catalog.approve_requests": "Approve product requests",
    "catalog.request_products": "Submit product requests",
    "catalog.configure_store_menu": "Enable products and set permitted prices",
    "catalog.set_availability": "Mark items available/unavailable (86)",
    # Orders / POS
    "pos.access": "Use the POS",
    "orders.create": "Create orders",
    "orders.view": "View orders",
    "orders.cancel": "Cancel orders",
    "orders.apply_discount": "Apply discounts",
    "orders.override_price": "Override prices",
    "orders.manual_integration_action": "Accept/reject third-party orders manually",
    "payments.take": "Take payments",
    "payments.refund": "Issue refunds",
    "cash.manage_session": "Open and close cash sessions",
    "cash.paid_in_out": "Record cash paid in/out",
    "kitchen.access": "Use the kitchen display",
    "delivery.access": "Delivery driver functions",
    # Integrations
    "integrations.manage_providers": "Set network-wide permitted providers",
    "integrations.configure_store": "Configure store integration connections",
    "integrations.view": "View integration status and logs",
    "api.manage_clients": "Manage external API clients",
    # Reporting / audit
    "reports.network": "Network-wide reports",
    "reports.franchise": "Franchise-level reports",
    "reports.store": "Store-level reports",
    "audit.view": "View audit log",
    "manager.authorize": "Authorize restricted operations with manager code",
}

ALL = set(PERMISSIONS)

ROLES = {
    "hq_admin": ("Headquarters administrator", "hq", ALL),
    "hq_staff": ("Headquarters staff", "hq", {
        "org.view_network", "catalog.manage_master", "catalog.approve_requests", "integrations.view",
        "reports.network", "reports.franchise", "reports.store", "orders.view", "audit.view",
    }),
    "franchisee_owner": ("Franchisee owner", "franchise", {
        "org.configure_store", "org.manage_devices", "people.manage_franchise_staff", "people.manage_store_staff",
        "catalog.request_products", "catalog.configure_store_menu", "catalog.set_availability",
        "integrations.configure_store", "integrations.view", "api.manage_clients", "reports.franchise", "reports.store",
        "orders.view", "orders.cancel", "orders.manual_integration_action", "payments.refund", "audit.view",
        "manager.authorize", "pos.access", "orders.create", "orders.apply_discount", "payments.take",
        "cash.manage_session", "cash.paid_in_out", "orders.override_price",
    }),
    "franchisee_manager": ("Franchisee manager", "franchise", {
        "org.configure_store", "org.manage_devices", "people.manage_store_staff", "catalog.request_products",
        "catalog.configure_store_menu", "catalog.set_availability", "integrations.configure_store",
        "integrations.view", "reports.franchise", "reports.store", "orders.view", "orders.cancel",
        "orders.manual_integration_action", "payments.refund", "manager.authorize", "pos.access", "orders.create",
        "orders.apply_discount", "payments.take", "cash.manage_session", "cash.paid_in_out",
    }),
    "store_manager": ("Store manager", "store", {
        "pos.access", "orders.create", "orders.view", "orders.cancel", "orders.apply_discount",
        "orders.override_price", "orders.manual_integration_action", "payments.take", "payments.refund",
        "cash.manage_session", "cash.paid_in_out", "catalog.set_availability", "people.manage_store_staff",
        "org.manage_devices", "integrations.view", "reports.store", "manager.authorize", "kitchen.access",
    }),
    "shift_supervisor": ("Shift supervisor", "store", {
        "pos.access", "orders.create", "orders.view", "orders.cancel", "orders.apply_discount", "payments.take",
        "payments.refund", "cash.manage_session", "cash.paid_in_out", "catalog.set_availability",
        "orders.manual_integration_action", "manager.authorize", "kitchen.access", "integrations.view",
    }),
    "cashier": ("Cashier", "store", {"pos.access", "orders.create", "orders.view", "payments.take", "cash.manage_session"}),
    "kitchen": ("Kitchen employee", "store", {"kitchen.access", "orders.view"}),
    "delivery": ("Delivery driver", "store", {"delivery.access", "orders.view", "pos.access"}),
}
