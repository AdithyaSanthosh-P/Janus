import React from 'react';
import { View, Text } from 'react-native';
import { LedgerData } from '../types/janus';
import { tokens, Tone } from '../theme/colors';
import { type } from '../theme/type';

interface LedgerFooterProps {
  ledger: LedgerData;
  isDark: boolean;
}

/**
 * The session's safety ledger: four counts in one strip split by hairlines.
 * A count takes its hue only when it is above zero; a zero stays grey.
 */
export const LedgerFooter: React.FC<LedgerFooterProps> = ({ ledger, isDark }) => {
  const t = tokens(isDark);
  const cells: { label: string; value: number; tone?: Tone }[] = [
    { label: 'Committed', value: ledger.committed, tone: t.positive },
    { label: 'Never sent', value: ledger.withdrawnBeforeSending, tone: t.negative },
    { label: 'Duplicates blocked', value: ledger.duplicatesBlocked },
    { label: 'Unconfirmed', value: ledger.unconfirmed, tone: t.caution },
  ];

  return (
    <View style={{ backgroundColor: t.ground, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 12 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 4, marginBottom: 8 }}>
        <Text style={[type.section, { color: t.ink2 }]}>Safety ledger</Text>
        <Text style={[type.footnote, { color: t.ink3 }]}>This session</Text>
      </View>
      <View style={{ flexDirection: 'row', backgroundColor: t.surface, borderRadius: 18, paddingVertical: 12 }}>
        {cells.map((cell, i) => (
          <View
            key={cell.label}
            style={{ flex: 1, paddingHorizontal: 10, borderLeftWidth: i === 0 ? 0 : 1, borderLeftColor: t.hairline }}
          >
            <Text style={[type.figure, { color: cell.value > 0 ? cell.tone?.ink ?? t.ink : t.ink3 }]}>{cell.value}</Text>
            <Text style={[type.caption, { color: t.ink2, marginTop: 2 }]} numberOfLines={2}>
              {cell.label}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
};
