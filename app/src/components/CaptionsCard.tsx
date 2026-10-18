import React, { useRef, useEffect } from 'react';
import { View, Text, ScrollView } from 'react-native';
import Animated, {
  FadeInUp,
  LinearTransition,
} from 'react-native-reanimated';
import { CaptionEntry } from '../types/janus';
import { LUCID_SPRING_CONFIG } from '../theme/motion';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: React Native Reusables Card container with conversational chat bubbles.
 * - Animation: Reanimated 3 with springified layout transitions and FadeInUp entry on streaming speech.
 * - Theming: Distinct styling for user, agent, and highlighted clarify queries across dark and light tokens.
 */

interface CaptionsCardProps {
  captions: CaptionEntry[];
  isDark: boolean;
  userSpeaking: boolean;
}

export const CaptionsCard: React.FC<CaptionsCardProps> = ({
  captions,
  isDark,
  userSpeaking,
}) => {
  const scrollViewRef = useRef<ScrollView>(null);

  useEffect(() => {
    scrollViewRef.current?.scrollToEnd({ animated: true });
  }, [captions.length, userSpeaking]);

  return (
    <View
      style={{
        backgroundColor: isDark ? '#18181b' : '#ffffff',
        borderRadius: 14,
        borderWidth: 1,
        borderColor: isDark ? '#27272a' : '#e2e8f0',
        padding: 12,
        maxHeight: 180,
      }}
    >
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 8,
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
          Live Conversation
        </Text>
        {userSpeaking && (
          <Text
            style={{
              fontSize: 11,
              fontWeight: '600',
              color: '#3b82f6',
            }}
          >
            User Speaking...
          </Text>
        )}
      </View>

      <ScrollView
        ref={scrollViewRef}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ gap: 8, paddingBottom: 4 }}
      >
        {captions.length === 0 ? (
          <Text
            style={{
              fontSize: 13,
              fontStyle: 'italic',
              color: isDark ? '#71717a' : '#94a3b8',
              textAlign: 'center',
              paddingVertical: 14,
            }}
          >
            Waiting for speech or starting demo playback...
          </Text>
        ) : (
          captions.map(caption => {
            const isUser = caption.sender === 'user';
            const isClarify = caption.kind === 'clarify';
            const isInterrupted = caption.isInterrupted;

            return (
              <Animated.View
                key={caption.id}
                entering={FadeInUp.springify()
                  .damping(LUCID_SPRING_CONFIG.damping)
                  .stiffness(LUCID_SPRING_CONFIG.stiffness)
                  .mass(LUCID_SPRING_CONFIG.mass)}
                layout={LinearTransition.springify()
                  .damping(LUCID_SPRING_CONFIG.damping)
                  .stiffness(LUCID_SPRING_CONFIG.stiffness)
                  .mass(LUCID_SPRING_CONFIG.mass)}
                style={{
                  alignSelf: isUser ? 'flex-end' : 'flex-start',
                  maxWidth: '88%',
                  backgroundColor: isUser
                    ? isDark
                      ? '#27272a'
                      : '#e0e7ff'
                    : isClarify
                    ? isDark
                      ? 'rgba(14, 165, 233, 0.2)'
                      : '#e0f2fe'
                    : isDark
                    ? '#222227'
                    : '#f1f5f9',
                  borderRadius: 12,
                  paddingHorizontal: 12,
                  paddingVertical: 8,
                  borderWidth: isClarify ? 1.5 : 1,
                  borderColor: isClarify
                    ? '#0284c7'
                    : isUser
                    ? isDark
                      ? '#3f3f46'
                      : '#c7d2fe'
                    : isDark
                    ? '#2e2e33'
                    : '#e2e8f0',
                }}
              >
                <View
                  style={{
                    flexDirection: 'row',
                    alignItems: 'center',
                    gap: 6,
                    marginBottom: 3,
                  }}
                >
                  <Text
                    style={{
                      fontSize: 10,
                      fontWeight: '700',
                      textTransform: 'uppercase',
                      color: isUser
                        ? isDark
                          ? '#93c5fd'
                          : '#3b82f6'
                        : isClarify
                        ? '#0284c7'
                        : isDark
                        ? '#a1a1aa'
                        : '#64748b',
                    }}
                  >
                    {isUser ? 'You' : isClarify ? 'Janus (Clarification)' : 'Janus'}
                  </Text>

                  {caption.kind && !isClarify && (
                    <Text
                      style={{
                        fontSize: 9,
                        fontWeight: '500',
                        color: isDark ? '#71717a' : '#94a3b8',
                      }}
                    >
                      [{caption.kind}]
                    </Text>
                  )}

                  {isInterrupted && (
                    <Text
                      style={{
                        fontSize: 9,
                        fontWeight: '700',
                        color: '#ef4444',
                      }}
                    >
                      [Interrupted]
                    </Text>
                  )}
                </View>

                <Text
                  style={{
                    fontSize: 13,
                    lineHeight: 18,
                    fontWeight: isClarify ? '600' : '400',
                    color: isClarify
                      ? isDark
                        ? '#38bdf8'
                        : '#0369a1'
                      : isDark
                      ? '#f4f4f5'
                      : '#0f172a',
                  }}
                >
                  {caption.text}
                </Text>
              </Animated.View>
            );
          })
        )}
      </ScrollView>
    </View>
  );
};
