// Adapted from bbpos apps/mobile/src/terminal (branch claude/adoring-dirac-y07721).
/**
 * Map Stripe Terminal SDK error codes (ErrorCode in
 * @stripe/stripe-terminal-react-native) to cashier-friendly messages.
 */
import type { TerminalError } from './types';

interface SdkErrorLike {
  code?: string;
  message?: string;
  apiError?: { code?: string; declineCode?: string; message?: string };
}

const MESSAGES: Record<string, Omit<TerminalError, 'code'>> = {
  BLUETOOTH_PERMISSION_DENIED: {
    message: 'Bluetooth permission is turned off for this app.',
    hint: 'Allow Bluetooth (and Nearby devices on Android) in Settings, then try again.',
    openSettings: true,
  },
  LOCATION_SERVICES_DISABLED: {
    message: 'Location services are turned off.',
    hint: 'Stripe requires location access to take card payments. Turn on Location in Settings.',
    openSettings: true,
  },
  BLUETOOTH_ERROR: { message: 'Bluetooth is unavailable.', hint: 'Make sure Bluetooth is turned on, then try again.', openSettings: true },
  BLUETOOTH_LOW_ENERGY_UNSUPPORTED: { message: 'This device does not support Bluetooth Low Energy.' },
  BLUETOOTH_SCAN_TIMED_OUT: {
    message: 'No reader found.',
    hint: 'Press the power button on the WisePad 3, keep it close to the phone, and make sure it is not connected to another device.',
  },
  BLUETOOTH_DISCONNECTED: { message: 'The reader disconnected.', hint: 'Check the reader is on, charged and in range.' },
  ALREADY_CONNECTED_TO_READER: { message: 'A reader is already connected.', hint: 'Disconnect it first to connect a different reader.' },
  NOT_CONNECTED_TO_READER: { message: 'No card reader is connected.', hint: 'Open Readers and connect your WisePad 3.' },
  READER_CONNECTED_TO_ANOTHER_DEVICE: {
    message: 'This reader is connected to another phone or tablet.',
    hint: 'Disconnect it there, or restart the reader, then try again.',
  },
  READER_BUSY: { message: 'The reader is busy.', hint: 'Wait a moment and try again.' },
  READER_COMMUNICATION_ERROR: { message: 'Could not communicate with the reader.', hint: 'Move the reader closer and try again.' },
  READER_BATTERY_CRITICALLY_LOW: { message: 'Reader battery is critically low.', hint: 'Charge the WisePad 3 via USB-C before taking payments.' },
  READER_BATTERY_LOW: { message: 'Reader battery is low.', hint: 'Charge the reader soon.' },
  READER_SOFTWARE_UPDATE_FAILED: { message: 'The reader software update failed.', hint: 'Keep the reader charged and nearby, then reconnect to retry.' },
  READER_SOFTWARE_UPDATE_FAILED_BATTERY_LOW: { message: 'Reader battery too low to update.', hint: 'Charge the reader to at least 50% and reconnect.' },
  READER_SOFTWARE_UPDATE_FAILED_INTERRUPTED: { message: 'The reader update was interrupted.', hint: 'Reconnect to resume the update.' },
  READER_SOFTWARE_UPDATE_FAILED_SERVER_ERROR: { message: 'Could not download the reader update.', hint: 'Check your internet connection and reconnect.' },
  UNSUPPORTED_READER_VERSION: { message: 'Reader software is out of date.', hint: 'Reconnect the reader to install the required update.' },
  CONNECTION_TOKEN_PROVIDER_ERROR: {
    message: 'Could not authorise the card reader.',
    hint: 'Check the internet connection and that a Stripe Terminal location is configured in Admin.',
  },
  SESSION_EXPIRED: { message: 'The reader session expired.', hint: 'Reconnect the reader.' },
  STRIPE_API_CONNECTION_ERROR: { message: 'Could not reach Stripe.', hint: 'Check your internet connection.' },
  REQUEST_TIMED_OUT: { message: 'The request timed out.', hint: 'Check your internet connection.' },
  CARD_READ_TIMED_OUT: { message: 'No card was presented in time.', hint: 'Try again and present the card promptly.' },
  CARD_INSERT_NOT_READ: { message: 'The card could not be read.', hint: 'Remove and re-insert the card chip-first.' },
  CARD_SWIPE_NOT_READ: { message: 'The swipe could not be read.', hint: 'Try inserting or tapping the card instead.' },
  CARD_REMOVED: { message: 'The card was removed too early.', hint: 'Leave the card inserted until prompted to remove it.' },
  CARD_LEFT_IN_READER: { message: 'Card left in the reader.', hint: 'Remove the card from the reader.' },
  DECLINED_BY_STRIPE_API: { message: 'The payment was declined.' },
  DECLINED_BY_READER: { message: 'The payment was declined by the card.' },
  CANCELED: { message: 'The payment was cancelled.' },
  CANCELED_BY_READER: { message: 'The payment was cancelled on the reader.' },
  TEST_CARD_IN_LIVEMODE: { message: 'A test card was used in live mode.', hint: 'Use a real card.' },
  CARD_NOT_SUPPORTED: { message: 'This card is not supported.', hint: 'Try a different card.' },
  READER_TAMPERED: { message: 'The reader reports it may have been tampered with.', hint: 'Stop using it and contact Stripe support.' },
};

const DECLINE_MESSAGES: Record<string, string> = {
  insufficient_funds: 'Insufficient funds.',
  card_declined: 'The card was declined.',
  generic_decline: 'The card was declined.',
  expired_card: 'The card has expired.',
  incorrect_pin: 'Incorrect PIN.',
  pin_try_exceeded: 'Too many PIN attempts.',
  lost_card: 'The card was declined.',
  stolen_card: 'The card was declined.',
  do_not_honor: 'The card issuer declined the payment.',
  transaction_not_allowed: 'This card does not allow this transaction.',
  card_velocity_exceeded: 'The card has exceeded its limit.',
  offline_pin_required: 'PIN required — please insert the card.',
  online_or_offline_pin_required: 'PIN required — please insert the card.',
};

export function toTerminalError(err: unknown): TerminalError {
  const e = (err ?? {}) as SdkErrorLike;
  const code = e.code ?? 'UNEXPECTED_SDK_ERROR';
  const declineCode = e.apiError?.declineCode || (e.apiError?.code === 'card_declined' ? 'card_declined' : null);
  const known = MESSAGES[code];
  return {
    code,
    message: declineCode ? (DECLINE_MESSAGES[declineCode] ?? 'The card was declined.') : (known?.message ?? e.message ?? 'Something went wrong with the card reader.'),
    hint: known?.hint ?? (declineCode ? 'Ask the customer for another payment method.' : undefined),
    declineCode,
    openSettings: known?.openSettings,
  };
}

export function isDecline(err: TerminalError): boolean {
  return Boolean(err.declineCode) || err.code === 'DECLINED_BY_STRIPE_API' || err.code === 'DECLINED_BY_READER';
}

export function isCancel(err: TerminalError): boolean {
  return err.code === 'CANCELED' || err.code === 'CANCELED_BY_READER';
}

export const DISCONNECT_REASONS: Record<string, string> = {
  disconnectRequested: 'Disconnected',
  rebootRequested: 'Reader restarting',
  securityReboot: 'Reader restarted for security',
  criticallyLowBattery: 'Reader battery critically low',
  poweredOff: 'Reader was turned off',
  bluetoothDisabled: 'Bluetooth was turned off',
  bluetoothSignalLost: 'Reader went out of range',
  peerRemovedPairingInformation: 'Reader was unpaired in system settings',
  idlePowerDown: 'Reader powered down after being idle',
  unknown: 'Reader disconnected unexpectedly',
};

export const READER_PROMPTS: Record<string, string> = {
  insertCard: 'Insert card',
  insertOrSwipeCard: 'Insert or swipe card',
  multipleContactlessCardsDetected: 'Multiple cards detected — present one card',
  removeCard: 'Remove card',
  retryCard: 'Try card again',
  swipeCard: 'Swipe card',
  tryAnotherCard: 'Try another card',
  tryAnotherReadMethod: 'Try another read method',
  checkMobileDevice: 'Check your phone to complete the payment',
  cardRemovedTooEarly: 'Card removed too early — try again',
};
