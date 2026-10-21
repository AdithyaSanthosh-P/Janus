import React from 'react';
import { View, Text } from 'react-native';
import Animated from 'react-native-reanimated';
import { DeviceCard } from './DeviceCard';
import { BackendType, DeviceStateData } from '../types/janus';
import { tokens } from '../theme/colors';
import { type } from '../theme/type';
import { AppLayoutTransition } from '../theme/motion';
import { SectionHeader } from './primitives';

interface DeviceTilesProps {
  devices: Record<string, DeviceStateData>;
  isDark: boolean;
  backend?: BackendType;
}

export const DeviceTiles: React.FC<DeviceTilesProps> = ({ devices, isDark, backend = 'unknown' }) => {
  const t = tokens(isDark);
  const deviceList: DeviceStateData[] = Object.values(devices);
  const source = backend === 'real' ? 'SmartThings Cloud' : backend === 'mock' ? 'Simulated' : undefined;

  return (
    <Animated.View layout={AppLayoutTransition}>
      <SectionHeader t={t} title="Home" note={source} />
      {deviceList.length === 0 ? (
        <View style={{ backgroundColor: t.surface, borderRadius: 18, padding: 16 }}>
          <Text style={[type.subhead, { color: t.ink2 }]}>No devices yet.</Text>
          <Text style={[type.footnote, { color: t.ink3, marginTop: 2 }]}>
            They appear when the agent reports their state.
          </Text>
        </View>
      ) : (
        // Two columns; an odd last tile spans the row rather than leaving a hole.
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
          {deviceList.map(dev => (
            <DeviceCard key={dev.device} device={dev} isDark={isDark} />
          ))}
        </View>
      )}
    </Animated.View>
  );
};
