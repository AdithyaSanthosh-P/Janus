import React, { useEffect } from 'react';
import { View, Text } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  interpolate,
} from 'react-native-reanimated';
import { ActionCardData } from '../types/janus';
import {
  CardEntryTransition,
  AppLayoutTransition,
  LUCID_SPRING_CONFIG,
} from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: React Native Reusables Card, Action Pills, and Status Badges.
 * - Animation: Reanimated 3 with horizontal strike-through line animation and opacity shift on cancelled actions.
 * - Theming: Full dark & light support with semantic status tokens (Emerald, Amber, Sky, Rose, Slate).
 */

interface ActionCardProps {
  card: ActionCardData;
  isDark: boolean;
}

export const ActionCard: React.FC<ActionCardProps> = ({ card, isDark }) => {
  const isCancelled = card.status === 'cancelled';
  const strikeProgress = useSharedValue(isCancelled ? 1 : 0);
  const cardOpacity = useSharedValue(isCancelled ? 0.7 : 1);

  useEffect(() => {
    if (isCancelled) {
      strikeProgress.value = withSpring(1, LUCID_SPRING_CONFIG);
      cardOpacity.value = withSpring(0.72, LUCID_SPRING_CONFIG);
    } else {
      strikeProgress.value = withSpring(0, LUCID_SPRING_CONFIG);
      cardOpacity.value = withSpring(1, LUCID_SPRING_CONFIG);
    }
  }, [isCancelled]);

  const animatedStrikeStyle = useAnimatedStyle(() => ({
    width: `${strikeProgress.value * 100}%`,
  }));

  const animatedCardStyle = useAnimatedStyle(() => ({
    opacity: cardOpacity.value,
  }));

  // Determine status pill styling & semantic messaging
  let statusBadgeBg = isDark ? '#27272a' : '#f1f5f9';
  let statusBadgeBorder = isDark ? '#3f3f46' : '#cbd5e1';
  let statusTextColor = isDark ? '#d4d4d8' : '#475569';
  let cardBorderColor = isDark ? '#27272a' : '#e2e8f0';

  if (card.status === 'cancelled') {
    statusBadgeBg = isDark ? 'rgba(239, 68, 68, 0.15)' : '#fee2e2';
    statusBadgeBorder = isDark ? 'rgba(239, 68, 68, 0.3)' : '#fca5a5';
    statusTextColor = '#ef4444';
    cardBorderColor = isDark ? '#3f3f46' : '#cbd5e1';
  } else if (card.status === 'confirmed' || card.status === 'already') {
    statusBadgeBg = isDark ? 'rgba(16, 185, 129, 0.15)' : '#ecfdf5';
    statusBadgeBorder = isDark ? 'rgba(16, 185, 129, 0.3)' : '#a7f3d0';
    statusTextColor = '#10b981';
    cardBorderColor = isDark ? 'rgba(16, 185, 129, 0.25)' : '#a7f3d0';
  } else if (card.status === 'held' || card.status === 'waiting_for_yes') {
    statusBadgeBg = isDark ? 'rgba(245, 158, 11, 0.15)' : '#fffbeb';
    statusBadgeBorder = isDark ? 'rgba(245, 158, 11, 0.3)' : '#fde68a';
    statusTextColor = '#f59e0b';
    cardBorderColor = isDark ? 'rgba(245, 158, 11, 0.3)' : '#fde68a';
  } else if (card.status === 'unconfirmed') {
    // Contract Rule: Show as "Sent, not confirmed" in amber, NEVER as done!
    statusBadgeBg = isDark ? 'rgba(245, 158, 11, 0.2)' : '#fef3c7';
    statusBadgeBorder = isDark ? '#f59e0b' : '#f59e0b';
    statusTextColor = '#d97706';
    cardBorderColor = '#f59e0b';
  } else if (card.status === 'sent') {
    statusBadgeBg = isDark ? 'rgba(59, 130, 246, 0.15)' : '#eff6ff';
    statusBadgeBorder = isDark ? 'rgba(59, 130, 246, 0.3)' : '#bfdbfe';
    statusTextColor = '#3b82f6';
  } else if (card.status === 'refused' || card.status === 'failed') {
    statusBadgeBg = isDark ? 'rgba(239, 68, 68, 0.15)' : '#fee2e2';
    statusBadgeBorder = isDark ? '#ef4444' : '#ef4444';
    statusTextColor = '#ef4444';
    cardBorderColor = '#ef4444';
  }

  return (
    <Animated.View
      entering={CardEntryTransition}
      layout={AppLayoutTransition}
      style={[
        {
          backgroundColor: isDark ? '#18181b' : '#ffffff',
          borderRadius: 14,
          borderWidth: 1.5,
          borderColor: cardBorderColor,
          padding: 12,
          position: 'relative',
          overflow: 'hidden',
        },
        animatedCardStyle,
      ]}
    >
      {/* Top Header: Tool Name, Security Icon & Status Badge */}
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 8,
        }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1 }}>
          {card.security && (
            <View
              style={{
                paddingHorizontal: 6,
                paddingVertical: 2,
                borderRadius: 6,
                backgroundColor: isDark ? 'rgba(245, 158, 11, 0.2)' : '#fef3c7',
                borderWidth: 1,
                borderColor: '#f59e0b',
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <Text style={{ fontSize: 10 }}>🔒</Text>
              <Text
                style={{
                  fontSize: 10,
                  fontWeight: '700',
                  color: '#f59e0b',
                  textTransform: 'uppercase',
                }}
              >
                Security
              </Text>
            </View>
          )}

          <Text
            style={{
              fontSize: 11,
              fontWeight: '600',
              color: isDark ? '#a1a1aa' : '#64748b',
              textTransform: 'uppercase',
              letterSpacing: 0.5,
            }}
          >
            {card.tool}
          </Text>
        </View>

        {/* Status Pill */}
        <View
          style={{
            paddingHorizontal: 8,
            paddingVertical: 3,
            borderRadius: 8,
            backgroundColor: statusBadgeBg,
            borderWidth: 1,
            borderColor: statusBadgeBorder,
          }}
        >
          <Text
            style={{
              fontSize: 11,
              fontWeight: '700',
              color: statusTextColor,
            }}
          >
            {card.statusText}
          </Text>
        </View>
      </View>

      {/* Action Title with Strike-Through Line */}
      <View style={{ position: 'relative', alignSelf: 'flex-start', marginVertical: 2 }}>
        <Text
          style={{
            fontSize: 16,
            fontWeight: '700',
            color: isCancelled
              ? isDark
                ? '#71717a'
                : '#94a3b8'
              : isDark
              ? '#fafafa'
              : '#0f172a',
          }}
        >
          {card.label}
        </Text>

        {/* Animated Strikethrough for "Never Sent" */}
        <Animated.View
          style={[
            {
              position: 'absolute',
              left: 0,
              top: '52%',
              height: 2.5,
              backgroundColor: '#ef4444',
              borderRadius: 1,
            },
            animatedStrikeStyle,
          ]}
        />
      </View>

      {/* Dynamic Content Details by State */}
      {/* 1. Cancelled / Never Sent Reason */}
      {card.status === 'cancelled' && card.cancelledReason && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(239, 68, 68, 0.1)' : '#fef2f2',
            borderLeftWidth: 3,
            borderLeftColor: '#ef4444',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '600',
              color: isDark ? '#fca5a5' : '#b91c1c',
            }}
          >
            Withdrawn: "{card.cancelledReason}"
          </Text>
          <Text
            style={{
              fontSize: 10,
              color: isDark ? '#a1a1aa' : '#7f1d1d',
              marginTop: 2,
            }}
          >
            Action was cancelled before sending to SmartThings cloud.
          </Text>
        </View>
      )}

      {/* 2. Held Reason (Contract: Display reason exactly as received) */}
      {card.status === 'held' && card.heldReason && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(245, 158, 11, 0.1)' : '#fffbeb',
            borderLeftWidth: 3,
            borderLeftColor: '#f59e0b',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '600',
              color: isDark ? '#fde68a' : '#b45309',
            }}
          >
            {card.heldReason}
          </Text>
        </View>
      )}

      {/* 3. Waiting for your yes (Confirmation Asked) */}
      {card.status === 'waiting_for_yes' && card.confirmationQuestion && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(14, 165, 233, 0.15)' : '#f0f9ff',
            borderLeftWidth: 3,
            borderLeftColor: '#0284c7',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '700',
              color: isDark ? '#7dd3fc' : '#0369a1',
            }}
          >
            {card.confirmationQuestion}
          </Text>
        </View>
      )}

      {/* 4. Sent to SmartThings */}
      {card.status === 'sent' && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(59, 130, 246, 0.1)' : '#eff6ff',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '500',
              color: isDark ? '#93c5fd' : '#1d4ed8',
            }}
          >
            Dispatched to SmartThings cloud... waiting for device confirmation.
          </Text>
        </View>
      )}

      {/* 5. Confirmed Result */}
      {card.status === 'confirmed' && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(16, 185, 129, 0.1)' : '#ecfdf5',
            borderLeftWidth: 3,
            borderLeftColor: '#10b981',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '600',
              color: isDark ? '#6ee7b7' : '#047857',
            }}
          >
            ✓ {card.resultSummary || 'Confirmed by SmartThings cloud'}
          </Text>
        </View>
      )}

      {/* 6. Already Done Result */}
      {card.status === 'already' && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(16, 185, 129, 0.1)' : '#ecfdf5',
            borderLeftWidth: 3,
            borderLeftColor: '#10b981',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '600',
              color: isDark ? '#6ee7b7' : '#047857',
            }}
          >
            ✓ {card.resultSummary || 'Already in target state; duplicate blocked'}
          </Text>
        </View>
      )}

      {/* 7. Unconfirmed (CRITICAL CONTRACT RULE: Never show as done!) */}
      {card.status === 'unconfirmed' && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(245, 158, 11, 0.15)' : '#fffbeb',
            borderLeftWidth: 3,
            borderLeftColor: '#f59e0b',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '700',
              color: '#d97706',
            }}
          >
            ⚠ {card.resultSummary || 'Command sent, but cloud did not confirm state'}
          </Text>
          <Text
            style={{
              fontSize: 10,
              color: isDark ? '#d4d4d8' : '#78350f',
              marginTop: 2,
            }}
          >
            Janus honesty safety layer: not marked as done until confirmed.
          </Text>
        </View>
      )}

      {/* 8. Failed / Refused */}
      {(card.status === 'failed' || card.status === 'refused') && (
        <View
          style={{
            marginTop: 8,
            padding: 8,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(239, 68, 68, 0.15)' : '#fef2f2',
            borderLeftWidth: 3,
            borderLeftColor: '#ef4444',
          }}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: '700',
              color: '#ef4444',
            }}
          >
            ✕ {card.resultSummary || 'Action failed'}
          </Text>
        </View>
      )}
    </Animated.View>
  );
};
