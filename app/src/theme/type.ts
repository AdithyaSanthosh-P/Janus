import { TextStyle } from 'react-native';

/**
 * Type scale on the system face. Tracking is size-specific: large text is
 * tightened, small text is left at zero or opened slightly.
 */
const scale = <T extends Record<string, TextStyle>>(styles: T) => styles;

export const type = scale({
  largeTitle: { fontSize: 28, lineHeight: 34, fontWeight: '700', letterSpacing: -0.6 },
  headline: { fontSize: 17, lineHeight: 22, fontWeight: '600', letterSpacing: -0.2 },
  body: { fontSize: 15, lineHeight: 21, fontWeight: '400' },
  subhead: { fontSize: 14, lineHeight: 19, fontWeight: '400' },
  section: { fontSize: 13, lineHeight: 18, fontWeight: '600' },
  footnote: { fontSize: 13, lineHeight: 18, fontWeight: '400' },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: '500', letterSpacing: 0.1 },
  figure: { fontSize: 22, lineHeight: 26, fontWeight: '600', letterSpacing: -0.3, fontVariant: ['tabular-nums'] },
});
