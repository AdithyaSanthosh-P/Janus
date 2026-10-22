import React from 'react';
import { Modal, View, Text, KeyboardAvoidingView, Platform, Pressable, ScrollView } from 'react-native';
import { X } from 'lucide-react-native';
import { Tokens } from '../theme/colors';
import { type } from '../theme/type';
import { Press } from './primitives';

interface SheetProps {
  visible: boolean;
  t: Tokens;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
}

/** A bottom sheet: a dimming scrim (tap to dismiss), a grabber, a title row and a scrolling body. */
export const Sheet: React.FC<SheetProps> = ({ visible, t, title, subtitle, onClose, children }) => (
  <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: t.scrim }}>
      <Pressable style={{ flex: 1 }} onPress={onClose} accessibilityLabel="Close" />
      <View style={{ backgroundColor: t.isDark ? '#1A1A1D' : t.ground, borderTopLeftRadius: 24, borderTopRightRadius: 24, maxHeight: '88%' }}>
        <View style={{ alignSelf: 'center', width: 36, height: 5, borderRadius: 3, backgroundColor: t.ink3, opacity: 0.5, marginTop: 8 }} />
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 12, paddingBottom: 8 }}>
          <View style={{ flex: 1, paddingRight: 12 }}>
            <Text style={[type.headline, { color: t.ink, fontSize: 20, lineHeight: 26 }]}>{title}</Text>
            {subtitle ? <Text style={[type.footnote, { color: t.ink2, marginTop: 2 }]}>{subtitle}</Text> : null}
          </View>
          <Press onPress={onClose} accessibilityLabel="Close" style={{ width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center', backgroundColor: t.raised }}>
            <X size={16} color={t.ink2} />
          </Press>
        </View>
        {/* flexShrink: inside a sheet capped by maxHeight, an unconstrained ScrollView
            took its full content height on Android and could not scroll until a tap
            re-laid it out (found on a phone, 22 Oct). */}
        <ScrollView
          style={{ flexGrow: 0, flexShrink: 1 }}
          showsVerticalScrollIndicator
          keyboardShouldPersistTaps="handled"
          nestedScrollEnabled
          contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 8, paddingBottom: 72, gap: 22 }}
        >
          {children}
        </ScrollView>
      </View>
    </KeyboardAvoidingView>
  </Modal>
);

/** An inset group: a heading above one rounded container whose rows are split by hairlines. */
export const Group: React.FC<{ t: Tokens; title?: string; footer?: string; children: React.ReactNode }> = ({ t, title, footer, children }) => {
  const rows = React.Children.toArray(children).filter(Boolean);
  return (
    <View>
      {title ? <Text style={[type.section, { color: t.ink2, paddingHorizontal: 4, marginBottom: 6 }]}>{title}</Text> : null}
      <View style={{ backgroundColor: t.isDark ? t.raised : t.surface, borderRadius: 14, overflow: 'hidden' }}>
        {rows.map((row, i) => (
          <View key={i}>
            {i > 0 && <View style={{ height: 1, marginLeft: 16, backgroundColor: t.hairline }} />}
            {row}
          </View>
        ))}
      </View>
      {footer ? <Text style={[type.footnote, { color: t.ink3, paddingHorizontal: 4, marginTop: 6 }]}>{footer}</Text> : null}
    </View>
  );
};
