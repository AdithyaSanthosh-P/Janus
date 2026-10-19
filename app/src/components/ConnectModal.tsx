import React, { useState } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';

/**
 * 3-Bullet Architecture Plan:
 * - Primitives: React Native Reusables Sheet / Modal with responsive inputs.
 * - Animation: Native modal slide transition with spring backdrop.
 * - Theming: Full dark (bg-zinc-900) & light (bg-white) tokens.
 */

interface ConnectModalProps {
  visible: boolean;
  isDark: boolean;
  isConnected: boolean;
  onClose: () => void;
  onConnect: (url: string, token: string, enableMic: boolean) => Promise<void>;
  onDisconnect: () => Promise<void>;
  onLaunchDemoMode: () => void;
}

export const ConnectModal: React.FC<ConnectModalProps> = ({
  visible,
  isDark,
  isConnected,
  onClose,
  onConnect,
  onDisconnect,
  onLaunchDemoMode,
}) => {
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [enableMic, setEnableMic] = useState(true);
  const [isConnecting, setIsConnecting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleConnect = async () => {
    if (!url.trim() || !token.trim()) {
      setErrorMessage('Please enter both LiveKit URL and Token.');
      return;
    }
    setErrorMessage(null);
    setIsConnecting(true);
    try {
      await onConnect(url, token, enableMic);
      onClose();
    } catch (err: any) {
      setErrorMessage(err?.message || 'Failed to connect.');
    } finally {
      setIsConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    await onDisconnect();
    onClose();
  };

  return (
    <Modal visible={visible} animationType="slide" transparent={true}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={{
          flex: 1,
          backgroundColor: 'rgba(0,0,0,0.6)',
          justifyContent: 'flex-end',
        }}
      >
        <View
          style={{
            backgroundColor: isDark ? '#18181b' : '#ffffff',
            borderTopLeftRadius: 20,
            borderTopRightRadius: 20,
            padding: 20,
            maxHeight: '90%',
          }}
        >
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: 16,
            }}
          >
            <View>
              <Text
                style={{
                  fontSize: 18,
                  fontWeight: '800',
                  color: isDark ? '#fafafa' : '#0f172a',
                }}
              >
                Connect to Janus Agent
              </Text>
              <Text
                style={{
                  fontSize: 12,
                  color: isDark ? '#a1a1aa' : '#64748b',
                  marginTop: 2,
                }}
              >
                LiveKit Room (janus.events topic)
              </Text>
            </View>

            <TouchableOpacity
              onPress={onClose}
              style={{
                padding: 8,
                borderRadius: 8,
                backgroundColor: isDark ? '#27272a' : '#f1f5f9',
              }}
            >
              <Text style={{ fontSize: 12, fontWeight: '700', color: isDark ? '#fafafa' : '#334155' }}>
                Close
              </Text>
            </TouchableOpacity>
          </View>

          <ScrollView showsVerticalScrollIndicator={false}>
            {errorMessage && (
              <View
                style={{
                  padding: 10,
                  borderRadius: 8,
                  backgroundColor: isDark ? 'rgba(239, 68, 68, 0.15)' : '#fee2e2',
                  borderWidth: 1,
                  borderColor: '#ef4444',
                  marginBottom: 12,
                }}
              >
                <Text style={{ fontSize: 12, color: '#ef4444', fontWeight: '600' }}>
                  {errorMessage}
                </Text>
              </View>
            )}

            {/* LiveKit URL Input */}
            <Text
              style={{
                fontSize: 12,
                fontWeight: '700',
                color: isDark ? '#d4d4d8' : '#334155',
                marginBottom: 4,
              }}
            >
              LiveKit Server URL
            </Text>
            <TextInput
              value={url}
              onChangeText={setUrl}
              placeholder="wss://janus-demo.livekit.cloud"
              placeholderTextColor={isDark ? '#71717a' : '#94a3b8'}
              autoCapitalize="none"
              autoCorrect={false}
              style={{
                backgroundColor: isDark ? '#27272a' : '#f8fafc',
                borderRadius: 10,
                borderWidth: 1,
                borderColor: isDark ? '#3f3f46' : '#cbd5e1',
                paddingHorizontal: 12,
                paddingVertical: 10,
                fontSize: 13,
                color: isDark ? '#fafafa' : '#0f172a',
                marginBottom: 14,
              }}
            />

            {/* Token Input */}
            <Text
              style={{
                fontSize: 12,
                fontWeight: '700',
                color: isDark ? '#d4d4d8' : '#334155',
                marginBottom: 4,
              }}
            >
              Access Token (Pasted or Scanned)
            </Text>
            <TextInput
              value={token}
              onChangeText={setToken}
              placeholder="eyJhbGciOi..."
              placeholderTextColor={isDark ? '#71717a' : '#94a3b8'}
              multiline
              numberOfLines={3}
              autoCapitalize="none"
              autoCorrect={false}
              style={{
                backgroundColor: isDark ? '#27272a' : '#f8fafc',
                borderRadius: 10,
                borderWidth: 1,
                borderColor: isDark ? '#3f3f46' : '#cbd5e1',
                paddingHorizontal: 12,
                paddingVertical: 10,
                fontSize: 12,
                color: isDark ? '#fafafa' : '#0f172a',
                height: 80,
                marginBottom: 14,
              }}
            />

            {/* Microphone Toggle */}
            <TouchableOpacity
              onPress={() => setEnableMic(!enableMic)}
              activeOpacity={0.8}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: 12,
                borderRadius: 10,
                backgroundColor: isDark ? '#27272a' : '#f1f5f9',
                marginBottom: 16,
              }}
            >
              <View>
                <Text
                  style={{
                    fontSize: 13,
                    fontWeight: '700',
                    color: isDark ? '#fafafa' : '#0f172a',
                  }}
                >
                  Enable Microphone
                </Text>
                <Text
                  style={{
                    fontSize: 11,
                    color: isDark ? '#a1a1aa' : '#64748b',
                  }}
                >
                  Publish phone mic to Janus agent
                </Text>
              </View>
              <View
                style={{
                  paddingHorizontal: 10,
                  paddingVertical: 4,
                  borderRadius: 6,
                  backgroundColor: enableMic
                    ? (isDark ? 'rgba(16, 185, 129, 0.2)' : '#dcfce7')
                    : (isDark ? '#27272a' : '#e2e8f0'),
                }}
              >
                <Text
                  style={{
                    fontSize: 11,
                    fontWeight: '800',
                    color: enableMic ? '#10b981' : (isDark ? '#71717a' : '#64748b'),
                  }}
                >
                  {enableMic ? 'ON' : 'OFF'}
                </Text>
              </View>
            </TouchableOpacity>

            {/* Connect / Disconnect Buttons */}
            {isConnected ? (
              <TouchableOpacity
                onPress={handleDisconnect}
                style={{
                  backgroundColor: '#ef4444',
                  paddingVertical: 12,
                  borderRadius: 10,
                  alignItems: 'center',
                  marginBottom: 10,
                }}
              >
                <Text style={{ fontSize: 14, fontWeight: '700', color: '#ffffff' }}>
                  Disconnect LiveKit Room
                </Text>
              </TouchableOpacity>
            ) : (
              <TouchableOpacity
                onPress={handleConnect}
                disabled={isConnecting}
                style={{
                  backgroundColor: '#3b82f6',
                  paddingVertical: 12,
                  borderRadius: 10,
                  alignItems: 'center',
                  marginBottom: 10,
                  opacity: isConnecting ? 0.7 : 1,
                }}
              >
                <Text style={{ fontSize: 14, fontWeight: '700', color: '#ffffff' }}>
                  {isConnecting ? 'Connecting...' : 'Join Room & Start Live Feed'}
                </Text>
              </TouchableOpacity>
            )}

            {/* Offline Fallback / Demo Launcher */}
            <TouchableOpacity
              onPress={() => {
                onLaunchDemoMode();
                onClose();
              }}
              style={{
                backgroundColor: isDark ? '#27272a' : '#e2e8f0',
                paddingVertical: 12,
                borderRadius: 10,
                alignItems: 'center',
                marginBottom: 16,
              }}
            >
              <Text
                style={{
                  fontSize: 13,
                  fontWeight: '700',
                  color: isDark ? '#fafafa' : '#334155',
                }}
              >
                Run Offline Demo Session (Fallback Mode)
              </Text>
            </TouchableOpacity>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
};
