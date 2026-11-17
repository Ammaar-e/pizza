/** Shapes returned by the Pizza platform API (subset used by the POS app). */
export interface Profile {
  id: number;
  username: string;
  name: string;
  role: string | null;
  role_level: 'hq' | 'franchise' | 'store' | null;
  franchisee_id: number | null;
  permissions: string[];
  store_ids: number[] | null;
}

export interface Store {
  id: number;
  number: string;
  name: string;
  city: string;
  currency: string;
  is_open_now: boolean;
  payment_config: {
    card_processor?: 'stripe' | 'bbpos' | 'none' | string;
    bbpos_api_url?: string;
    /** Optional URL the tablets use when it differs from the server-to-server URL. */
    bbpos_public_url?: string;
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

export interface PosSession {
  access: string;
  refresh: string;
  user: Profile;
  store: Store;
}

export interface DeviceCredential {
  token: string;
  id: number;
  name: string;
  store: number;
  store_number: string;
  pending?: boolean;
}

export interface MenuProduct {
  id: number;
  sku: string;
  name: string;
  is_pizza: boolean;
  is_available: boolean;
  allow_half_and_half: boolean;
  max_toppings: number;
  price_cents: number | null;
  description: string;
  sizes: { code: string; name: string; price_cents: number; modifier_multiplier_percent: number }[];
  crusts: { code: string; name: string; price_cents: number }[];
  default_crust: string | null;
  default_modifiers: { code: string; name: string; quantity: number; removable: boolean }[];
  modifier_groups: {
    code: string;
    name: string;
    min_select: number;
    max_select: number;
    max_quantity_per_option: number;
    free_selections: number;
    allow_half: boolean;
    half_price_percent: number;
    scale_with_size: boolean;
    modifiers: { code: string; name: string; price_cents: number; is_topping: boolean; is_available: boolean }[];
  }[];
}

export interface Menu {
  categories: { code: string; name: string; products: MenuProduct[] }[];
}

export type Placement = 'whole' | 'left' | 'right';
export interface LineMod {
  code: string;
  placement: Placement;
  quantity: number;
}
export interface CartLine {
  key: string;
  product: MenuProduct;
  size?: string;
  crust?: string;
  quantity: number;
  modifiers: LineMod[];
  removed: string[];
  special_instructions: string;
}

export interface PromotionRef {
  promotion_id: number;
  name: string;
  code?: string | null;
  amount_cents?: number;
  reason?: string;
  message?: string;
}

export interface Quote {
  subtotal_cents: number;
  discount_cents: number;
  tax_cents: number;
  total_cents: number;
  charges: { code: string; name: string; amount_cents: number }[];
  lines?: { line: number; unit_price_cents?: number; line_total_cents?: number; issues?: { message: string }[] }[];
  promotions?: { applied: PromotionRef[]; rejected: PromotionRef[]; available: PromotionRef[] };
  delivery?: { eligible: boolean; message?: string; fee_cents?: number } | null;
  issues?: { message: string }[];
}

export interface Payment {
  id: string;
  method: 'cash' | 'card' | 'online' | 'provider';
  processor: string;
  status: 'pending' | 'authorized' | 'captured' | 'partially_refunded' | 'refunded' | 'failed' | 'voided';
  amount_cents: number;
  tip_cents: number;
  change_cents?: number;
  card_brand?: string;
  card_last4?: string;
  refunded_cents?: number;
  created_at: string;
}

export interface Order {
  id: string;
  order_number: number;
  status: string;
  order_type: 'pickup' | 'delivery' | 'dine_in' | string;
  source: string;
  payment_status: 'unpaid' | 'partially_paid' | 'paid' | 'partially_refunded' | 'refunded' | string;
  subtotal_cents: number;
  discount_cents: number;
  tax_cents: number;
  total_cents: number;
  amount_paid_cents?: number;
  tip_cents?: number;
  customer_snapshot?: { name?: string; phone?: string } | null;
  customer_name?: string;
  item_count?: number;
  items?: {
    id: number;
    name: string;
    quantity: number;
    line_total_cents: number;
    size_name?: string;
    crust_name?: string;
    removed?: string[];
    special_instructions?: string;
    modifiers?: { name: string; placement: string; quantity: number; is_default?: boolean }[];
  }[];
  payments?: Payment[];
  placed_at: string;
  [k: string]: unknown;
}

export interface Page<T> {
  count: number;
  results: T[];
}
