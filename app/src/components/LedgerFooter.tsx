import React from 'react';
import { View, Text } from 'react-native';
import Animated from 'react-native-reanimated';
import {
  ShieldCheck,
  CircleCheck,
  Ban,
  CheckCheck,
  CircleAlert,
} from 'lucide-react-native';
import { LedgerData } from '../types/janus';
import { AppLayoutTransition } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: Safety audit counters bar with semantic vector icons pinned to the bottom.
 * - Animation: Reanimated 3 layout transition on count changes.
 * - Theming: Semantic badge colors across dark and light tokens.
 */

interface LedgerFooterProps {
  ledger: LedgerData;
  isDark: boolean;
}

export const LedgerFooter: React.FC<LedgerFooterProps> = ({ ledger, isDark }) => {
  return (
    <Animated.View
      layout={AppLayoutTransition}
      style={{
        backgroundColor: isDark ? '#09090b' : '#ffffff',
        borderTopWidth: 1,
        borderTopColor: isDark ? '#27272a' : '#e2e8f0',
        paddingHorizontal: 16,
        paddingVertical: 10,
      }}
    >
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 6,
        }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <ShieldCheck size={14} color="#10b981" />
          <Text
            style={{
              fontSize: 11,
              fontWeight: '700',
              textTransform: 'uppercase',
              letterSpacing: 0.8,
              color: isDark ? '#a1a1aa' : '#64748b',
            }}
          >
            Janus Safety Ledger
          </Text>
        </View>

        <Text
          style={{
            fontSize: 10,
            fontWeight: '600',
            color: isDark ? '#71717a' : '#94a3b8',
          }}
        >
          Real-time Audit
        </Text>
      </View>

      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 6,
        }}
      >
        {/* Committed */}
        <View
          style={{
            flex: 1,
            alignItems: 'center',
            paddingVertical: 6,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(16, 185, 129, 0.12)' : '#ecfdf5',
            borderWidth: 1,
            borderColor: isDark ? 'rgba(16, 185, 129, 0.25)' : '#a7f3d0',
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <CircleCheck size={11} color="#10b981" />
            <Text
              style={{
                fontSize: 16,
                fontWeight: '800',
                color: '#10b981',
              }}
            >
              {ledger.committed}
            </Text>
          </View>
          <Text
            style={{
              fontSize: 9,
              fontWeight: '700',
              color: isDark ? '#a7f3d0' : '#065f46',
              textTransform: 'uppercase',
              marginTop: 1,
            }}
          >
            Committed
          </Text>
        </View>

        {/* Never Sent / Withdrawn */}
        <View
          style={{
            flex: 1,
            alignItems: 'center',
            paddingVertical: 6,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(239, 68, 68, 0.12)' : '#fee2e2',
            borderWidth: 1,
            borderColor: isDark ? 'rgba(239, 68, 68, 0.25)' : '#fca5a5',
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <Ban size={11} color="#ef4444" />
            <Text
              style={{
                fontSize: 16,
                fontWeight: '800',
                color: '#ef4444',
              }}
            >
              {ledger.withdrawnBeforeSending}
            </Text>
          </View>
          <Text
            style={{
              fontSize: 9,
              fontWeight: '700',
              color: isDark ? '#fca5a5' : '#991b1b',
              textTransform: 'uppercase',
              marginTop: 1,
            }}
          >
            Never Sent
          </Text>
        </View>

        {/* Duplicates Blocked */}
        <View
          style={{
            flex: 1,
            alignItems: 'center',
            paddingVertical: 6,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(59, 130, 246, 0.12)' : '#eff6ff',
            borderWidth: 1,
            borderColor: isDark ? 'rgba(59, 130, 246, 0.25)' : '#bfdbfe',
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <CheckCheck size={11} color="#3b82f6" />
            <Text
              style={{
                fontSize: 16,
                fontWeight: '800',
                color: '#3b82f6',
              }}
            >
              {ledger.duplicatesBlocked}
            </Text>
          </View>
          <Text
            style={{
              fontSize: 9,
              fontWeight: '700',
              color: isDark ? '#bfdbfe' : '#1e40af',
              textTransform: 'uppercase',
              marginTop: 1,
            }}
          >
            Duplicates
          </Text>
        </View>

        {/* Unconfirmed (Honesty) */}
        <View
          style={{
            flex: 1,
            alignItems: 'center',
            paddingVertical: 6,
            borderRadius: 8,
            backgroundColor: isDark ? 'rgba(245, 158, 11, 0.12)' : '#fffbeb',
            borderWidth: 1,
            borderColor: isDark ? 'rgba(245, 158, 11, 0.25)' : '#fde68a',
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <CircleAlert size={11} color="#f59e0b" />
            <Text
              style={{
                fontSize: 16,
                fontWeight: '800',
                color: '#f59e0b',
              }}
            >
              {ledger.unconfirmed}
            </Text>
          </View>
          <Text
            style={{
              fontSize: 9,
              fontWeight: '700',
              color: isDark ? '#fde68a' : '#92400e',
              textTransform: 'uppercase',
              marginTop: 1,
            }}
          >
            Unconfirmed
          </Text>
        </View>
      </View>
    </Animated.View>
  );
};

