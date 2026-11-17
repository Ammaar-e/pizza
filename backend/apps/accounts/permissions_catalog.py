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
    "org.manage_devices": "Register devices for own stores (may require approval)",
    "devices.view": "View device registry, status and alerts",
    "devices.administer": "Rename, reassign, deactivate devices and revoke credentials",
    "devices.approve": "Approve pending device registrations (franchise: subject to HQ policy)",
    "devices.configure_profiles": "Manage device configuration profiles and assign them",
    "devices.local_settings": "Change locally overridable device settings",
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
    "cash.paid_in_out": "Record cash additions, paid-outs, expenses and transfers",
    "cash.approve": "Approve cash discrepancies and register closings",
    "cash.correct": "Authorize cash corrections",
    "cash.review": "Review all register sessions of a store",
    "promotions.view": "View promotions",
    "promotions.manage_network": "Create and publish network-wide promotions (HQ)",
    "promotions.manage_franchise": "Create franchise promotions within HQ policy",
    "promotions.manage_store": "Manage store promotions when authorized by the franchisee",
    "promotions.apply_code": "Enter coupon / promotion codes at checkout",
    "dispatch.view": "View the delivery dispatch board",
    "dispatch.manage": "Assign deliveries and manage driver availability",
    "drivers.manage": "Manage driver profiles",
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
        "devices.view", "promotions.view", "promotions.manage_network", "dispatch.view", "cash.review",
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
        "devices.view", "devices.administer", "devices.approve", "devices.local_settings", "cash.approve",
        "cash.correct", "cash.review", "promotions.view", "promotions.manage_franchise", "promotions.apply_code",
        "dispatch.view", "dispatch.manage", "drivers.manage",
    }),
    "franchisee_manager": ("Franchisee manager", "franchise", {
        "org.configure_store", "org.manage_devices", "people.manage_store_staff", "catalog.request_products",
        "catalog.configure_store_menu", "catalog.set_availability", "integrations.configure_store",
        "integrations.view", "reports.franchise", "reports.store", "orders.view", "orders.cancel",
        "orders.manual_integration_action", "payments.refund", "manager.authorize", "pos.access", "orders.create",
        "orders.apply_discount", "payments.take", "cash.manage_session", "cash.paid_in_out",
        "devices.view", "devices.local_settings", "cash.approve", "cash.correct", "cash.review", "promotions.view",
        "promotions.manage_franchise", "promotions.apply_code", "dispatch.view", "dispatch.manage", "drivers.manage",
    }),
    "store_manager": ("Store manager", "store", {
        "pos.access", "orders.create", "orders.view", "orders.cancel", "orders.apply_discount",
        "orders.override_price", "orders.manual_integration_action", "payments.take", "payments.refund",
        "cash.manage_session", "cash.paid_in_out", "catalog.set_availability", "people.manage_store_staff",
        "org.manage_devices", "integrations.view", "reports.store", "manager.authorize", "kitchen.access",
        "devices.view", "devices.local_settings", "cash.approve", "cash.correct", "cash.review", "promotions.view",
        "promotions.manage_store", "promotions.apply_code", "dispatch.view", "dispatch.manage", "drivers.manage",
    }),
    "shift_supervisor": ("Shift supervisor", "store", {
        "pos.access", "orders.create", "orders.view", "orders.cancel", "orders.apply_discount", "payments.take",
        "payments.refund", "cash.manage_session", "cash.paid_in_out", "catalog.set_availability",
        "orders.manual_integration_action", "manager.authorize", "kitchen.access", "integrations.view",
        "cash.approve", "cash.correct", "promotions.apply_code", "dispatch.view", "dispatch.manage",
    }),
    "cashier": ("Cashier", "store", {"pos.access", "orders.create", "orders.view", "payments.take", "cash.manage_session",
                                     "promotions.apply_code", "dispatch.view"}),
    "kitchen": ("Kitchen employee", "store", {"kitchen.access", "orders.view"}),
    "delivery": ("Delivery driver", "store", {"delivery.access", "orders.view", "pos.access"}),
}
