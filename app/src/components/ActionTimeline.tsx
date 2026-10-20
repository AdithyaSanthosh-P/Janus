import React from 'react';
import { View, Text } from 'react-native';
import Animated from 'react-native-reanimated';
import { ActionCard } from './ActionCard';
import { ActionCardData } from '../types/janus';
import { AppLayoutTransition } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: React Native Reusables Timeline list container.
 * - Animation: Reanimated 3 with continuous layout transition as cards shift states.
 * - Theming: Clean dark (text-zinc-400) and light (text-slate-600) tokens.
 */

interface ActionTimelineProps {
  actions: ActionCardData[];
  isDark: boolean;
}

export const ActionTimeline: React.FC<ActionTimelineProps> = ({
  actions,
  isDark,
}) => {
  return (
    <Animated.View layout={AppLayoutTransition} style={{ flex: 1 }}>
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 10,
        }}
      >
        <Text
          style={{
            fontSize: 12,
            fontWeight: '700',
            textTransform: 'uppercase',
            letterSpacing: 0.8,
            color: isDark ? '#a1a1aa' : '#64748b',
          }}
        >
          Action Timeline
        </Text>
        <Text
          style={{
            fontSize: 11,
            fontWeight: '600',
            color: isDark ? '#71717a' : '#94a3b8',
          }}
        >
          {actions.length} {actions.length === 1 ? 'Action' : 'Actions'}
        </Text>
      </View>

      {actions.length === 0 ? (
        <View
          style={{
            padding: 24,
            borderRadius: 14,
            borderWidth: 1,
            borderStyle: 'dashed',
            borderColor: isDark ? '#27272a' : '#cbd5e1',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text
            style={{
              fontSize: 13,
              fontStyle: 'italic',
              color: isDark ? '#71717a' : '#94a3b8',
              textAlign: 'center',
            }}
          >
            No actions planned yet.{'\n'}Speak a command or tap "Play Demo".
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
