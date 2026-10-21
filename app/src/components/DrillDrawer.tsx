import React, { useState, useEffect } from 'react';
import { View, Text, Switch, StyleSheet } from 'react-native';
import {
  AlertTriangle,
  WifiOff,
  Clock,
  Brain,
  PowerOff,
  Lock,
  LockOpen,
  RotateCcw,
  DoorOpen,
  Warehouse,
} from 'lucide-react-native';
import { drillService } from '../services/drillService';
import { DrillState } from '../utils/drillBuilder';
import { tokens, Tone } from '../theme/colors';
import { type } from '../theme/type';
import { Press } from './primitives';
import { Group, Sheet } from './Sheet';

interface DrillDrawerProps {
  visible: boolean;
  isDark: boolean;
  onClose: () => void;
}

export const DrillDrawer: React.FC<DrillDrawerProps> = ({
  visible,
  isDark,
  onClose,
}) => {
  const [drillState, setDrillState] = useState<DrillState>(drillService.getState());
  const [isAnyActive, setIsAnyActive] = useState<boolean>(drillService.isAnyActive());

  useEffect(() => {
    const unsubscribe = drillService.subscribe((state, anyActive) => {
      setDrillState(state);
      setIsAnyActive(anyActive);
    });
    return unsubscribe;
  }, []);

  const handleToggle = (name: keyof DrillState, value: boolean) => {
    drillService.setDrill(name, value);
  };

  const handleExternalUnlock = () => {
    return drillService.sendExternalCommand('unlock');
  };

  const handleExternalLock = () => {
    return drillService.sendExternalCommand('lock');
  };

  const handleGarage = (command: 'open' | 'close') => {
    return drillService.sendGarage(command);
  };

  const handleOffer = (kind: 'garage_open' | 'door_unlocked') => {
    return drillService.sendOffer(kind);
  };

  const handleResetAll = () => {
    drillService.resetAll(true);
  };

  const t = tokens(isDark);
  const [flash, setFlash] = useState<Record<string, 'sent' | 'failed'>>({});
  const fire = async (title: string, send: () => Promise<boolean> | void) => {
    const ok = await send();
    setFlash(f => ({ ...f, [title]: ok === false ? 'failed' : 'sent' }));
    setTimeout(() => setFlash(f => { const { [title]: _gone, ...rest } = f; return rest; }), 2500);
  };

  const toggles: { key: keyof DrillState; title: string; detail: string; tone: Tone; Glyph: typeof WifiOff }[] = [
    { key: 'st_unreachable', title: 'SmartThings down', detail: 'The cloud call fails; nothing is sent.', tone: t.negative, Glyph: WifiOff },
    { key: 'st_no_confirm', title: 'Device never confirms', detail: 'The write is accepted but never confirmed.', tone: t.caution, Glyph: AlertTriangle },
    { key: 'st_latency', title: 'Slow SmartThings (3s)', detail: 'Every cloud call takes 3 seconds longer.', tone: t.info, Glyph: Clock },
    { key: 'model_slow', title: 'Slow model (4s)', detail: 'The model takes 4 seconds longer; Janus covers the wait.', tone: t.info, Glyph: Brain },
    { key: 'model_down', title: 'Model down', detail: 'The reasoning service is unavailable.', tone: t.negative, Glyph: PowerOff },
  ];

  const commands: { title: string; onPress: () => Promise<boolean> | void; Glyph: typeof WifiOff }[][] = [
    [
      { title: 'Someone else unlocks the front door', onPress: handleExternalUnlock, Glyph: LockOpen },
      { title: '…locks it', onPress: handleExternalLock, Glyph: Lock },
      { title: 'Someone else opens the garage', onPress: () => handleGarage('open'), Glyph: DoorOpen },
      { title: '…closes it', onPress: () => handleGarage('close'), Glyph: Warehouse },
    ],
    [
      { title: 'Offer: close the garage', onPress: () => handleOffer('garage_open'), Glyph: Warehouse },
      { title: 'Offer: lock the front door', onPress: () => handleOffer('door_unlocked'), Glyph: Lock },
    ],
  ];

  const commandRow = (c: { title: string; onPress: () => Promise<boolean> | void; Glyph: typeof WifiOff }) => (
    <Press key={c.title} onPress={() => fire(c.title, c.onPress)} pressedStyle={{ backgroundColor: t.pressed }} style={styles.row}>
      <c.Glyph size={17} color={t.ink2} />
      <Text style={[type.body, { color: t.ink, flex: 1 }]}>{c.title}</Text>
      {flash[c.title] && (
        <Text style={[type.caption, { color: flash[c.title] === 'sent' ? t.positive.ink : t.negative.ink, fontWeight: '600' }]}>
          {flash[c.title] === 'sent' ? 'Sent' : 'Not sent'}
        </Text>
      )}
    </Press>
  );

  return (
    <Sheet
      visible={visible}
      t={t}
      title="Failure drills"
      subtitle="Break something on purpose and watch Janus report it honestly."
      onClose={onClose}
    >
      <Group t={t} title="Faults">
        {toggles.map(d => (
          <View key={d.key} style={styles.row}>
            <d.Glyph size={17} color={drillState[d.key] ? d.tone.mark : t.ink2} />
            <View style={{ flex: 1 }}>
              <Text style={[type.body, { color: t.ink }]}>{d.title}</Text>
              <Text style={[type.footnote, { color: t.ink2 }]}>{d.detail}</Text>
            </View>
            <Switch
              value={drillState[d.key]}
              onValueChange={val => handleToggle(d.key, val)}
              trackColor={{ false: t.pressed, true: d.tone.mark }}
              thumbColor="#FFFFFF"
            />
          </View>
        ))}
      </Group>

      <Group t={t} title="Someone else changes a device" footer="As if by hand or from the SmartThings app, bypassing Janus.">
        {commands[0].map(commandRow)}
      </Group>

      <Group t={t} title="Janus speaks first" footer="Janus offers an action only if a device gives it something to offer.">
        {commands[1].map(commandRow)}
      </Group>

      <Press
        onPress={handleResetAll}
        style={[styles.reset, { backgroundColor: isAnyActive ? t.negative.mark : t.isDark ? t.raised : t.surface }]}
      >
        <RotateCcw size={16} color={isAnyActive ? '#FFFFFF' : t.ink2} />
        <Text style={[type.body, { fontWeight: '600', color: isAnyActive ? '#FFFFFF' : t.ink2 }]}>All off</Text>
      </Press>
    </Sheet>
  );
};

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    minHeight: 50,
  },
  reset: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    height: 50,
    borderRadius: 14,
  },
});
