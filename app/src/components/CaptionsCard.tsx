import React, { useRef, useEffect } from 'react';
import { View, Text, ScrollView } from 'react-native';
import Animated from 'react-native-reanimated';
import { CaptionEntry } from '../types/janus';
import { tokens } from '../theme/colors';
import { type } from '../theme/type';
import { AppLayoutTransition, BubbleEntryTransition } from '../theme/motion';
import { SectionHeader } from './primitives';

interface CaptionsCardProps {
  captions: CaptionEntry[];
  isDark: boolean;
  userSpeaking: boolean;
}

/** The conversation as a transcript: your words in a bubble on the right, Janus's on the left. */
export const CaptionsCard: React.FC<CaptionsCardProps> = ({ captions, isDark, userSpeaking }) => {
  const t = tokens(isDark);
  const scrollViewRef = useRef<ScrollView>(null);

  useEffect(() => {
    scrollViewRef.current?.scrollToEnd({ animated: true });
  }, [captions.length, userSpeaking]);

  return (
    <View>
      <SectionHeader t={t} title="Conversation" />
      <View style={{ backgroundColor: t.surface, borderRadius: 18, paddingHorizontal: 16, paddingVertical: 12, maxHeight: 210 }}>
        <ScrollView ref={scrollViewRef} showsVerticalScrollIndicator={false} contentContainerStyle={{ gap: 10, paddingVertical: 2 }}>
          {captions.length === 0 ? (
            <Text style={[type.subhead, { color: t.ink3, paddingVertical: 12 }]}>
              Say something to Janus, or play the demo file.
            </Text>
          ) : (
            captions.map(caption => {
              const isUser = caption.sender === 'user';
              const isClarify = caption.kind === 'clarify';
              return (
                <Animated.View
                  key={caption.id}
                  entering={BubbleEntryTransition}
                  layout={AppLayoutTransition}
                  style={
                    isUser
                      ? { alignSelf: 'flex-end', maxWidth: '84%', backgroundColor: t.raised, borderRadius: 18, paddingHorizontal: 14, paddingVertical: 8 }
                      : { alignSelf: 'flex-start', maxWidth: '92%', paddingVertical: 2 }
                  }
                >
                  <Text style={[type.body, { color: isClarify ? t.info.ink : t.ink, fontWeight: isClarify ? '500' : '400' }]}>
                    {caption.text}
                  </Text>
                  {caption.isInterrupted && (
                    <Text style={[type.caption, { color: t.ink3, marginTop: 2 }]}>Interrupted</Text>
                  )}
                </Animated.View>
              );
            })
          )}
        </ScrollView>
      </View>
    </View>
  );
};
