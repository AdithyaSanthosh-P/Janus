import React from 'react';
import { View, Text, TouchableOpacity } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withSpring,
  withRepeat,
} from 'react-native-reanimated';
import {
  Sun,
  Moon,
  Radio,
  Play,
  Pause,
  RotateCcw,
  Mic,
  MicOff,
} from 'lucide-react-native';
import { LUCID_SPRING_CONFIG } from '../theme/motion';
import { BackendType } from '../types/janus';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: Header container with vector action icons, status pills, and controls.
 * - Animation: Reanimated 3 with scale pulse on active mic status and spring transitions on mode switch.
 * - Theming: Native dark (bg-zinc-950, border-zinc-800) and light (bg-white, border-slate-200) support.
 */

interface HeaderProps {
  backend: BackendType;
  connectionStatus: 'disconnected' | 'connecting' | 'connected' | 'demo' | 'error';
  userSpeaking: boolean;
  isDark: boolean;
  demoPlaying: boolean;
  demoSpeed: number;
  isDrillActive?: boolean;
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
  isDark,
  demoPlaying,
  demoSpeed,
  isDrillActive = false,
  onToggleTheme,
  onOpenConnectModal,
  onToggleDemoPlay,
  onToggleDemoSpeed,
  onResetDemo,
  onOpenDrillDrawer,
}) => {
  const micPulse = useSharedValue(1);

  React.useEffect(() => {
    if (userSpeaking) {
      micPulse.value = withRepeat(
        withSpring(1.15, LUCID_SPRING_CONFIG),
        -1,
        true
      );
    } else {
      micPulse.value = withSpring(1.0, LUCID_SPRING_CONFIG);
    }
  }, [userSpeaking]);

  const micAnimatedStyle = useAnimatedStyle(() => ({
    transform: [{ scale: micPulse.value }],
  }));

  // Connection badge color
  const statusColor =
    connectionStatus === 'connected'
      ? '#10b981'
      : connectionStatus === 'demo'
      ? '#8b5cf6'
      : connectionStatus === 'connecting'
      ? '#f59e0b'
      : '#71717a';

  const statusLabel =
    connectionStatus === 'connected'
      ? 'Live'
      : connectionStatus === 'demo'
      ? 'Demo Mode'
      : connectionStatus === 'connecting'
      ? 'Connecting...'
      : 'Offline';

  return (
    <View
      style={{
        backgroundColor: isDark ? '#09090b' : '#ffffff',
        borderBottomWidth: 1,
        borderBottomColor: isDark ? '#27272a' : '#e2e8f0',
        paddingHorizontal: 16,
        paddingTop: 12,
        paddingBottom: 10,
      }}
    >
      {/* Top row: App branding, Backend Badge, Theme Toggle */}
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text
            style={{
              fontSize: 22,
              fontWeight: '800',
              letterSpacing: -0.5,
              color: isDark ? '#fafafa' : '#0f172a',
            }}
          >
            Janus
          </Text>

          {/* Backend Badge per contract: Long-press in live mode opens Failure Drills drawer */}
          <TouchableOpacity
            onLongPress={connectionStatus !== 'demo' ? onOpenDrillDrawer : undefined}
            delayLongPress={500}
            activeOpacity={connectionStatus !== 'demo' ? 0.75 : 1}
            style={{
              paddingHorizontal: 8,
              paddingVertical: 3,
              borderRadius: 12,
              backgroundColor:
                backend === 'real'
                  ? isDark
                    ? 'rgba(16, 185, 129, 0.15)'
                    : '#ecfdf5'
                  : isDark
                  ? 'rgba(245, 158, 11, 0.15)'
                  : '#fffbeb',
              borderWidth: 1,
              borderColor:
                backend === 'real'
                  ? isDark
                    ? 'rgba(16, 185, 129, 0.3)'
                    : '#a7f3d0'
                  : isDark
                  ? 'rgba(245, 158, 11, 0.3)'
                  : '#fde68a',
            }}
          >
            <Text
              style={{
                fontSize: 10,
                fontWeight: '700',
                textTransform: 'uppercase',
                letterSpacing: 0.5,
                color: backend === 'real' ? '#10b981' : '#f59e0b',
              }}
            >
              {backend === 'real'
                ? 'REAL · SmartThings Cloud'
                : 'MOCK · Simulated'}
            </Text>
          </TouchableOpacity>

          {/* DRILL ON Badge: Highlighted when any failure injection toggle is active */}
          {isDrillActive && (
            <TouchableOpacity
              onPress={onOpenDrillDrawer}
              activeOpacity={0.8}
              style={{
                paddingHorizontal: 6,
                paddingVertical: 2,
                borderRadius: 8,
                backgroundColor: 'rgba(239, 68, 68, 0.2)',
                borderWidth: 1,
                borderColor: '#ef4444',
                flexDirection: 'row',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <View
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 3,
                  backgroundColor: '#ef4444',
                }}
              />
              <Text
                style={{
                  fontSize: 10,
                  fontWeight: '800',
                  color: '#ef4444',
                  letterSpacing: 0.5,
                }}
              >
                DRILL ON
              </Text>
            </TouchableOpacity>
          )}
        </View>

        {/* Quick controls: Connect, Theme Toggle */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <TouchableOpacity
            onPress={onOpenConnectModal}
            activeOpacity={0.7}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 5,
              paddingHorizontal: 10,
              paddingVertical: 6,
              borderRadius: 8,
              backgroundColor: isDark ? '#27272a' : '#f1f5f9',
            }}
          >
            <Radio size={13} color={isDark ? '#fafafa' : '#334155'} />
            <Text
              style={{
                fontSize: 12,
                fontWeight: '600',
                color: isDark ? '#fafafa' : '#334155',
              }}
            >
              Connect
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            onPress={onToggleTheme}
            activeOpacity={0.7}
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: isDark ? '#27272a' : '#f1f5f9',
            }}
          >
            {isDark ? (
              <Sun size={16} color="#fbbf24" />
            ) : (
              <Moon size={16} color="#6366f1" />
            )}
          </TouchableOpacity>
        </View>
      </View>

      {/* Secondary Row: Connection status, Mic indicator, Demo controls */}
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginTop: 8,
        }}
      >
        {/* Left: Connection pill + Live Mic Status */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 5,
              paddingHorizontal: 8,
              paddingVertical: 4,
              borderRadius: 8,
              backgroundColor: isDark ? '#18181b' : '#f1f5f9',
              borderWidth: 1,
              borderColor: isDark ? '#27272a' : '#e2e8f0',
            }}
          >
            <View
              style={{
                width: 7,
                height: 7,
                borderRadius: 4,
                backgroundColor: statusColor,
              }}
            />
            <Text
              style={{
                fontSize: 11,
                fontWeight: '600',
                color: isDark ? '#e4e4e7' : '#334155',
              }}
            >
              {statusLabel}
            </Text>
          </View>

          {/* Dynamic Mic Waveform Pill */}
          <Animated.View
            style={[
              {
                flexDirection: 'row',
                alignItems: 'center',
                gap: 5,
                paddingHorizontal: 8,
                paddingVertical: 4,
                borderRadius: 8,
                backgroundColor: userSpeaking
                  ? isDark
                    ? 'rgba(16, 185, 129, 0.2)'
                    : '#dcfce7'
                  : isDark
                  ? '#18181b'
                  : '#f1f5f9',
                borderWidth: 1,
                borderColor: userSpeaking
                  ? '#10b981'
                  : isDark
                  ? '#27272a'
                  : '#e2e8f0',
              },
              micAnimatedStyle,
            ]}
          >
            {userSpeaking ? (
              <Mic size={12} color="#10b981" />
            ) : (
              <MicOff size={12} color={isDark ? '#71717a' : '#94a3b8'} />
            )}
            <Text
              style={{
                fontSize: 11,
                fontWeight: '600',
                color: userSpeaking
                  ? '#10b981'
                  : isDark
                  ? '#71717a'
                  : '#94a3b8',
              }}
            >
              {userSpeaking ? 'Listening' : 'Muted'}
            </Text>
          </Animated.View>
        </View>

        {/* Right: Demo playback toolbar */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <TouchableOpacity
            onPress={onToggleDemoPlay}
            activeOpacity={0.7}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 4,
              paddingHorizontal: 9,
              paddingVertical: 5,
              borderRadius: 7,
              backgroundColor: demoPlaying ? '#ef4444' : '#10b981',
            }}
          >
            {demoPlaying ? (
              <Pause size={12} color="#ffffff" fill="#ffffff" />
            ) : (
              <Play size={12} color="#ffffff" fill="#ffffff" />
            )}
            <Text style={{ fontSize: 11, fontWeight: '700', color: '#ffffff' }}>
              {demoPlaying ? 'Pause' : 'Play Demo'}
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            onPress={onToggleDemoSpeed}
            activeOpacity={0.7}
            style={{
              paddingHorizontal: 7,
              paddingVertical: 5,
              borderRadius: 7,
              backgroundColor: isDark ? '#27272a' : '#e2e8f0',
            }}
          >
            <Text
              style={{
                fontSize: 11,
                fontWeight: '700',
                color: isDark ? '#fafafa' : '#334155',
              }}
            >
              {demoSpeed}x
            </Text>
          </TouchableOpacity>

          <TouchableOpacity
            onPress={onResetDemo}
            activeOpacity={0.7}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 4,
              paddingHorizontal: 7,
              paddingVertical: 5,
              borderRadius: 7,
              backgroundColor: isDark ? '#27272a' : '#e2e8f0',
            }}
          >
            <RotateCcw size={11} color={isDark ? '#fafafa' : '#334155'} />
            <Text
              style={{
                fontSize: 11,
                fontWeight: '700',
                color: isDark ? '#fafafa' : '#334155',
              }}
            >
              Reset
            </Text>
          </TouchableOpacity>
        </View>
      </View>
    </View>
  );
};

export default Header;
