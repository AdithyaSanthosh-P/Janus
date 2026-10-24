import React from 'react';
import { View, Text } from 'react-native';
import Animated from 'react-native-reanimated';
import { ActionCard } from './ActionCard';
import { ActionCardData } from '../types/janus';
import { tokens } from '../theme/colors';
import { type } from '../theme/type';
import { AppLayoutTransition } from '../theme/motion';
import { SectionHeader } from './primitives';

interface ActionTimelineProps {
  actions: ActionCardData[];
  isDark: boolean;
  title?: string;
  /** The count shown beside the title (defaults to the number of cards). */
  note?: string;
}

export const ActionTimeline: React.FC<ActionTimelineProps> = ({ actions, isDark, title = 'Actions', note }) => {
  const t = tokens(isDark);
  return (
    <Animated.View layout={AppLayoutTransition}>
      <SectionHeader t={t} title={title} note={note ?? (actions.length > 0 ? String(actions.length) : undefined)} />
      {actions.length === 0 ? (
        <View style={{ backgroundColor: t.surface, borderRadius: 18, padding: 16 }}>
          <Text style={[type.subhead, { color: t.ink2 }]}>Nothing planned yet.</Text>
          <Text style={[type.footnote, { color: t.ink3, marginTop: 2 }]}>
            Every action Janus plans appears here before anything is sent.
          </Text>
        </View>
      ) : (
        <View style={{ gap: 10 }}>
          {actions.map(card => (
            <ActionCard key={card.callId} card={card} isDark={isDark} />
          ))}
        </View>
      )}
    </Animated.View>
  );
};
