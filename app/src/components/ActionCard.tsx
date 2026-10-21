import React, { useEffect } from 'react';
import { View, Text } from 'react-native';
import Animated, { useSharedValue, useAnimatedStyle, withSpring } from 'react-native-reanimated';
import { Lock, Thermometer, Lightbulb, Warehouse, WashingMachine, Activity, ShieldAlert } from 'lucide-react-native';
import { ActionCardData } from '../types/janus';
import { cardCategory } from '../utils/labels';
import { statusTone, Tokens, tokens, Tone } from '../theme/colors';
import { type } from '../theme/type';
import { CardEntryTransition, AppLayoutTransition, SPRING } from '../theme/motion';
import { Pill } from './primitives';

interface ActionCardProps {
  card: ActionCardData;
  isDark: boolean;
}

type StepState = 'done' | 'current' | 'stopped' | 'off';
interface Step {
  label: string;
  state: StepState;
  tone: Tone;
}

/**
 * The commit track: how far this action got, Planned → Sent → Confirmed.
 * Derived from the card's status alone, so it can never show a step the
 * agent did not report: a withdrawn action visibly stops before "Sent".
 */
const commitTrack = (card: ActionCardData, t: Tokens): Step[] => {
  const tone = statusTone(card.status, t);
  const n = t.neutral;
  switch (card.status) {
    case 'sent':
      return [
        { label: 'Planned', state: 'done', tone: n },
        { label: 'Sent', state: 'current', tone },
        { label: 'Confirmed', state: 'off', tone: n },
      ];
    case 'confirmed':
      return [
        { label: 'Planned', state: 'done', tone },
        { label: 'Sent', state: 'done', tone },
        { label: 'Confirmed', state: 'done', tone },
      ];
    case 'already':
      return [
        { label: 'Planned', state: 'done', tone },
        { label: 'Not sent again', state: 'off', tone: n },
        { label: 'Already done', state: 'done', tone },
      ];
    case 'unconfirmed':
      return [
        { label: 'Planned', state: 'done', tone: n },
        { label: 'Sent', state: 'done', tone },
        { label: 'Not confirmed', state: 'stopped', tone },
      ];
    case 'cancelled':
      return [
        { label: 'Planned', state: 'done', tone: n },
        { label: 'Never sent', state: 'stopped', tone },
        { label: 'Confirmed', state: 'off', tone: n },
      ];
    case 'failed':
    case 'refused':
      return [
        { label: 'Planned', state: 'done', tone: n },
        { label: 'Sent', state: 'done', tone: n },
        { label: card.status === 'refused' ? 'Refused' : 'Failed', state: 'stopped', tone },
      ];
    default:
      // planned, held, waiting for a yes
      return [
        { label: card.status === 'waiting_for_yes' ? 'Waiting for yes' : card.status === 'held' ? 'Held' : 'Planned', state: 'current', tone },
        { label: 'Sent', state: 'off', tone: n },
        { label: 'Confirmed', state: 'off', tone: n },
      ];
  }
};

const CommitTrack: React.FC<{ steps: Step[]; t: Tokens; skipped: boolean }> = ({ steps, t, skipped }) => {
  const node = (step: Step) => (
    <View
      style={{
        width: 10,
        height: 10,
        borderRadius: 5,
        backgroundColor: step.state === 'done' || step.state === 'stopped' ? step.tone.mark : t.surface,
        borderWidth: 2,
        borderColor: step.state === 'off' ? t.hairline : step.tone.mark,
      }}
    />
  );
  // The segment into a step is drawn in its hue once the action got there;
  // a duplicate that was never sent again keeps its segments grey.
  const segment = (step: Step) => (
    <View style={{ flex: 1, height: 2, marginHorizontal: 4, borderRadius: 1, backgroundColor: step.state !== 'off' && !skipped ? step.tone.mark : t.hairline }} />
  );
  const label = (step: Step, align: 'left' | 'center' | 'right') => {
    const strong = step.state === 'stopped' || step.state === 'current';
    return (
      <Text
        style={[type.caption, { flex: 1, textAlign: align, color: strong ? step.tone.ink : step.state === 'off' ? t.ink3 : t.ink2, fontWeight: strong ? '600' : '500' }]}
        numberOfLines={1}
      >
        {step.label}
      </Text>
    );
  };
  return (
    <View style={{ marginTop: 16 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        {node(steps[0])}
        {segment(steps[1])}
        {node(steps[1])}
        {segment(steps[2])}
        {node(steps[2])}
      </View>
      <View style={{ flexDirection: 'row', marginTop: 6 }}>
        {label(steps[0], 'left')}
        {label(steps[1], 'center')}
        {label(steps[2], 'right')}
      </View>
    </View>
  );
};

const detailFor = (card: ActionCardData): { text: string; note?: string } | null => {
  switch (card.status) {
    case 'cancelled':
      return card.cancelledReason ? { text: card.cancelledReason, note: 'Withdrawn before anything reached SmartThings.' } : null;
    case 'held':
      return card.heldReason ? { text: card.heldReason } : null;
    case 'waiting_for_yes':
      return card.confirmationQuestion ? { text: card.confirmationQuestion } : null;
    case 'sent':
      return { text: 'Sent to SmartThings. Waiting for the device to confirm.' };
    case 'confirmed':
      return { text: card.resultSummary || 'Confirmed by SmartThings.' };
    case 'already':
      return { text: card.resultSummary || 'Already that way, so nothing was sent again.' };
    case 'unconfirmed':
      return {
        text: card.resultSummary || 'Command sent, but the device did not confirm it.',
        note: 'Not marked done until the device confirms.',
      };
    case 'failed':
    case 'refused':
      return { text: card.resultSummary || 'Action failed.' };
    default:
      return null;
  }
};

export const ActionCard: React.FC<ActionCardProps> = ({ card, isDark }) => {
  const t = tokens(isDark);
  const tone = statusTone(card.status, t);
  const isCancelled = card.status === 'cancelled';
  const strike = useSharedValue(isCancelled ? 1 : 0);
  const [labelWidth, setLabelWidth] = React.useState(0);

  useEffect(() => {
    strike.value = withSpring(isCancelled ? 1 : 0, SPRING);
  }, [isCancelled]);

  const strikeStyle = useAnimatedStyle(() => ({ width: strike.value * labelWidth }));

  const glyphColor = t.ink3;
  let glyph = <Activity size={15} color={glyphColor} />;
  if (card.tool.includes('lock')) glyph = <Lock size={15} color={glyphColor} />;
  else if (card.tool.includes('cooling') || card.tool.includes('setpoint') || card.tool.includes('thermostat')) glyph = <Thermometer size={15} color={glyphColor} />;
  else if (card.tool.includes('switch') || card.tool.includes('light')) glyph = <Lightbulb size={15} color={glyphColor} />;
  else if (card.tool.includes('garage')) glyph = <Warehouse size={15} color={glyphColor} />;
  else if (card.tool.includes('washer') || card.tool.includes('error_code')) glyph = <WashingMachine size={15} color={glyphColor} />;

  const detail = detailFor(card);
  const detailInk =
    card.status === 'waiting_for_yes' || card.status === 'failed' || card.status === 'refused' || card.status === 'unconfirmed'
      ? tone.ink
      : t.ink;

  return (
    <Animated.View
      entering={CardEntryTransition}
      layout={AppLayoutTransition}
      style={{ backgroundColor: t.surface, borderRadius: 18, padding: 16 }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1 }}>
          {glyph}
          <Text style={[type.footnote, { color: t.ink2 }]} numberOfLines={1}>
            {cardCategory(card.tool, card.kind)}
          </Text>
          {card.security && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 3, marginLeft: 2 }}>
              <ShieldAlert size={13} color={t.caution.ink} />
              <Text style={[type.caption, { color: t.caution.ink, fontWeight: '600' }]}>Security</Text>
            </View>
          )}
        </View>
        <Pill text={card.statusText} ink={tone.ink} bg={tone.bg} dot={tone.mark} />
      </View>

      <View style={{ alignSelf: 'flex-start', marginTop: 12 }}>
        <Text
          onLayout={e => setLabelWidth(e.nativeEvent.layout.width)}
          style={[type.headline, { color: isCancelled ? t.ink3 : t.ink }]}
        >
          {card.label}
        </Text>
        <Animated.View
          pointerEvents="none"
          style={[
            { position: 'absolute', left: 0, top: 10, height: 2, borderRadius: 1, backgroundColor: t.negative.mark },
            strikeStyle,
          ]}
        />
      </View>

      {detail && (
        <View style={{ marginTop: 6 }}>
          <Text style={[type.subhead, { color: detailInk }]}>{detail.text}</Text>
          {detail.note && <Text style={[type.footnote, { color: t.ink2, marginTop: 2 }]}>{detail.note}</Text>}
        </View>
      )}

      <CommitTrack steps={commitTrack(card, t)} t={t} skipped={card.status === 'already'} />
    </Animated.View>
  );
};
