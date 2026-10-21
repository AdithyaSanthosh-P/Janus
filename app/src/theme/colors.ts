import { ActionCardStatus } from '../types/janus';

/**
 * Design tokens. Flat surfaces separated by one tone step, no shadows;
 * colour is spent only on status, never on chrome.
 *
 * Honesty colours (fixed meaning across the app):
 *   positive (green) - confirmed by the device, or already that way
 *   caution  (amber) - held, or sent but not confirmed
 *   negative (red)   - never sent, failed, refused
 *   info     (blue)  - in flight, or waiting for the user's yes
 */

export interface Tone {
  mark: string; // dots, lines, the strike-through
  ink: string; // text in that hue
  bg: string; // pale pill fill
}

export interface Tokens {
  isDark: boolean;
  ground: string;
  surface: string;
  raised: string;
  pressed: string;
  hairline: string;
  ink: string;
  ink2: string;
  ink3: string;
  inverseInk: string;
  inverseFill: string;
  scrim: string;
  positive: Tone;
  caution: Tone;
  negative: Tone;
  info: Tone;
  neutral: Tone;
}

const dark: Tokens = {
  isDark: true,
  ground: '#000000',
  surface: '#141416',
  raised: '#222225',
  pressed: '#2C2C30',
  hairline: '#2A2A2E',
  ink: '#F5F5F7',
  ink2: '#A1A1A6',
  ink3: '#6E6E73',
  inverseInk: '#000000',
  inverseFill: '#F5F5F7',
  scrim: 'rgba(0, 0, 0, 0.6)',
  positive: { mark: '#30D158', ink: '#4CD964', bg: 'rgba(48, 209, 88, 0.14)' },
  caution: { mark: '#FF9F0A', ink: '#FFB340', bg: 'rgba(255, 159, 10, 0.15)' },
  negative: { mark: '#FF453A', ink: '#FF6961', bg: 'rgba(255, 69, 58, 0.15)' },
  info: { mark: '#0A84FF', ink: '#5EAEFF', bg: 'rgba(10, 132, 255, 0.16)' },
  neutral: { mark: '#8E8E93', ink: '#A1A1A6', bg: '#222225' },
};

const light: Tokens = {
  isDark: false,
  ground: '#F2F2F7',
  surface: '#FFFFFF',
  raised: '#EEEEF2',
  pressed: '#E3E3E8',
  hairline: '#E3E3E8',
  ink: '#1D1D1F',
  ink2: '#6E6E73',
  ink3: '#AEAEB2',
  inverseInk: '#FFFFFF',
  inverseFill: '#1D1D1F',
  scrim: 'rgba(0, 0, 0, 0.35)',
  positive: { mark: '#34C759', ink: '#1E7A34', bg: '#E4F6E8' },
  caution: { mark: '#FF9500', ink: '#A64B00', bg: '#FFF1DC' },
  negative: { mark: '#FF3B30', ink: '#C4001A', bg: '#FDE8E9' },
  info: { mark: '#007AFF', ink: '#0058BC', bg: '#E6F0FD' },
  neutral: { mark: '#8E8E93', ink: '#6E6E73', bg: '#EEEEF2' },
};

export const tokens = (isDark: boolean): Tokens => (isDark ? dark : light);

export const statusTone = (status: ActionCardStatus, t: Tokens): Tone => {
  switch (status) {
    case 'confirmed':
    case 'already':
      return t.positive;
    case 'held':
    case 'unconfirmed':
      return t.caution;
    case 'cancelled':
    case 'failed':
    case 'refused':
      return t.negative;
    case 'sent':
    case 'waiting_for_yes':
      return t.info;
    default:
      return t.neutral;
  }
};
