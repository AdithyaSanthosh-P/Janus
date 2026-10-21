import React from 'react';
import { View, Text } from 'react-native';
import { Sun, Moon, Play, Pause, RotateCcw, Camera, CameraOff } from 'lucide-react-native';
import { BackendType } from '../types/janus';
import { tokens } from '../theme/colors';
import { type } from '../theme/type';
import { Press } from './primitives';
import { ThinkingOrb } from '../orb/ThinkingOrb';
import { OrbState } from '../orb/types';
import { WaveformIndicator } from './WaveformIndicator';

interface HeaderProps {
  backend: BackendType;
  connectionStatus: 'disconnected' | 'connecting' | 'connected' | 'demo' | 'error';
  userSpeaking: boolean;
  orbState: OrbState;
  isDark: boolean;
  demoPlaying: boolean;
  demoSpeed: number;
  isDrillActive?: boolean;
  isCameraOn?: boolean;
  onToggleCamera?: () => void;
  onToggleTheme: () => void;
  onOpenConnectModal: () => void;
  onToggleDemoPlay: () => void;
  onToggleDemoSpeed: () => void;
  onResetDemo: () => void;
  onOpenDrillDrawer?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  backend,
  connectionStatus,
  userSpeaking,
  orbState,
  isDark,
  demoPlaying,
  demoSpeed,
  isDrillActive = false,
  isCameraOn = false,
  onToggleCamera,
  onToggleTheme,
  onOpenConnectModal,
  onToggleDemoPlay,
  onToggleDemoSpeed,
  onResetDemo,
  onOpenDrillDrawer,
}) => {
  const t = tokens(isDark);
  const live = connectionStatus === 'connected';

  const connection =
    live
      ? { dot: t.positive.mark, text: 'Live' }
      : connectionStatus === 'demo'
      ? { dot: t.neutral.mark, text: 'Demo file' }
      : connectionStatus === 'connecting'
      ? { dot: t.caution.mark, text: 'Connecting…' }
      : { dot: t.ink3, text: 'Offline' };

  // Never claim a backend the app hasn't been told about: "Not connected"
  // until the agent's session_started, and a recording is labelled as one.
  const backendText =
    connectionStatus === 'demo'
      ? 'Recorded session'
      : backend === 'real'
      ? 'Real · SmartThings Cloud'
      : backend === 'mock'
      ? 'Mock · Simulated'
      : live
      ? 'Waiting for the agent'
      : '';
  const backendInk = backend === 'mock' && connectionStatus !== 'demo' ? t.caution.ink : t.ink2;

  const iconButton = { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: t.surface } as const;
  const chip = { height: 32, paddingHorizontal: 12, borderRadius: 16, flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: t.surface } as const;

  return (
    <View style={{ backgroundColor: t.ground, paddingHorizontal: 20, paddingTop: 14, paddingBottom: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <ThinkingOrb state={orbState} size={34} dark={isDark} label={`Janus, ${orbState}`} />
          <Text style={[type.largeTitle, { color: t.ink }]}>Janus</Text>
        </View>

        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {live && onToggleCamera && (
            <Press
              onPress={onToggleCamera}
              accessibilityLabel={isCameraOn ? 'Turn camera off' : 'Turn camera on'}
              style={[iconButton, isCameraOn && { backgroundColor: t.info.bg }]}
              pressedStyle={{ backgroundColor: t.pressed }}
            >
              {isCameraOn ? <Camera size={17} color={t.info.ink} /> : <CameraOff size={17} color={t.ink2} />}
            </Press>
          )}
          <Press
            onPress={onToggleTheme}
            accessibilityLabel={isDark ? 'Light appearance' : 'Dark appearance'}
            style={iconButton}
            pressedStyle={{ backgroundColor: t.pressed }}
          >
            {isDark ? <Sun size={17} color={t.ink2} /> : <Moon size={17} color={t.ink2} />}
          </Press>
          <Press onPress={onOpenConnectModal} style={[chip, { height: 36, borderRadius: 18, paddingHorizontal: 14 }]} pressedStyle={{ backgroundColor: t.pressed }}>
            <Text style={[type.subhead, { color: t.ink, fontWeight: '600' }]}>{live ? 'Connected' : 'Connect'}</Text>
          </Press>
        </View>
      </View>

      {/* Status line: connection and backend (long-press opens the failure drills live) */}
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12, minHeight: 32 }}>
        <Press
          onLongPress={connectionStatus !== 'demo' ? onOpenDrillDrawer : undefined}
          delayLongPress={500}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 }}
        >
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: connection.dot }} />
          <Text style={[type.footnote, { color: t.ink, fontWeight: '600' }]}>{connection.text}</Text>
          <Text style={[type.footnote, { color: backendInk, flexShrink: 1 }]} numberOfLines={1}>
            {backendText}
          </Text>
        </Press>

        {live ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {userSpeaking && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <WaveformIndicator active size="sm" color={t.ink} />
                <Text style={[type.footnote, { color: t.ink2 }]}>Listening</Text>
              </View>
            )}
            {isDrillActive ? (
              <Press onPress={onOpenDrillDrawer} style={[chip, { height: 28, paddingHorizontal: 10, backgroundColor: t.negative.bg }]}>
                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: t.negative.mark }} />
                <Text style={[type.caption, { color: t.negative.ink, fontWeight: '600' }]}>Drill on</Text>
              </Press>
            ) : (
              <Press onPress={onOpenDrillDrawer} accessibilityLabel="Failure drills" style={[chip, { height: 28, paddingHorizontal: 10 }]} pressedStyle={{ backgroundColor: t.pressed }}>
                <Text style={[type.caption, { color: t.ink2, fontWeight: '600' }]}>Drills</Text>
              </Press>
            )}
          </View>
        ) : (
          // Sample-file playback controls; they do nothing live, so live hides them.
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Press
              onPress={onToggleDemoPlay}
              style={[chip, { backgroundColor: t.inverseFill }]}
              accessibilityLabel={demoPlaying ? 'Pause' : 'Play Demo'}
            >
              {demoPlaying ? <Pause size={13} color={t.inverseInk} fill={t.inverseInk} /> : <Play size={13} color={t.inverseInk} fill={t.inverseInk} />}
              <Text style={[type.footnote, { color: t.inverseInk, fontWeight: '600' }]}>{demoPlaying ? 'Pause' : 'Play Demo'}</Text>
            </Press>
            <Press onPress={onToggleDemoSpeed} style={chip} pressedStyle={{ backgroundColor: t.pressed }}>
              <Text style={[type.footnote, { color: t.ink, fontWeight: '600', fontVariant: ['tabular-nums'] }]}>{demoSpeed}x</Text>
            </Press>
            <Press onPress={onResetDemo} style={[chip, { width: 32, paddingHorizontal: 0, justifyContent: 'center' }]} pressedStyle={{ backgroundColor: t.pressed }} accessibilityLabel="Reset">
              <RotateCcw size={14} color={t.ink2} />
            </Press>
          </View>
        )}
      </View>
    </View>
  );
};

export default Header;
