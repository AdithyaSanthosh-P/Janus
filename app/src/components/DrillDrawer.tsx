import React, { useState, useEffect } from 'react';
import {
  Modal,
  View,
  Text,
  TouchableOpacity,
  Switch,
  ScrollView,
  StyleSheet,
} from 'react-native';
import {
  X,
  AlertTriangle,
  WifiOff,
  Clock,
  Brain,
  PowerOff,
  Lock,
  LockOpen,
  RotateCcw,
  ShieldAlert,
} from 'lucide-react-native';
import { drillService } from '../services/drillService';
import { DrillState } from '../utils/drillBuilder';

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
    drillService.sendExternalCommand('unlock');
  };

  const handleExternalLock = () => {
    drillService.sendExternalCommand('lock');
  };

  const handleGarage = (command: 'open' | 'close') => {
    drillService.sendGarage(command);
  };

  const handleOffer = (kind: 'garage_open' | 'door_unlocked') => {
    drillService.sendOffer(kind);
  };

  const handleResetAll = () => {
    drillService.resetAll(true);
  };

  const bgColor = isDark ? '#18181b' : '#ffffff';
  const borderColor = isDark ? '#27272a' : '#e2e8f0';
  const textColor = isDark ? '#fafafa' : '#0f172a';
  const subTextColor = isDark ? '#a1a1aa' : '#64748b';
  const sectionBg = isDark ? '#27272a' : '#f8fafc';

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>
        <View style={[styles.sheet, { backgroundColor: bgColor, borderColor }]}>
          {/* Header */}
          <View style={[styles.header, { borderBottomColor: borderColor }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <ShieldAlert size={20} color="#f59e0b" />
              <Text style={[styles.title, { color: textColor }]}>
                Failure Drills
              </Text>
              <View style={styles.topicBadge}>
                <Text style={styles.topicText}>janus.control</Text>
              </View>
            </View>

            <TouchableOpacity
              onPress={onClose}
              style={[styles.closeBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9' }]}
              accessibilityLabel="Close drill drawer"
            >
              <X size={18} color={isDark ? '#e4e4e7' : '#334155'} />
            </TouchableOpacity>
          </View>

          <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
            <Text style={[styles.description, { color: subTextColor }]}>
              Simulate failure modes to verify that Janus reports failures and non-confirmed outcomes honestly.
            </Text>

            {/* Section 1: Failure Toggles */}
            <View style={styles.section}>
              <Text style={[styles.sectionTitle, { color: textColor }]}>
                Active Fault Injections
              </Text>

              {/* 1. SmartThings Down */}
              <View style={[styles.toggleRow, { backgroundColor: sectionBg, borderColor }]}>
                <View style={styles.toggleTextContainer}>
                  <View style={styles.toggleHeader}>
                    <WifiOff size={16} color="#ef4444" />
                    <Text style={[styles.toggleTitle, { color: textColor }]}>
                      SmartThings down
                    </Text>
                  </View>
                  <Text style={[styles.toggleSubtitle, { color: subTextColor }]}>
                    st_unreachable · cloud connection fails, nothing sent
                  </Text>
                </View>
                <Switch
                  value={drillState.st_unreachable}
                  onValueChange={(val) => handleToggle('st_unreachable', val)}
                  trackColor={{ false: isDark ? '#3f3f46' : '#cbd5e1', true: '#ef4444' }}
                  thumbColor="#ffffff"
                />
              </View>

              {/* 2. Device never confirms */}
              <View style={[styles.toggleRow, { backgroundColor: sectionBg, borderColor }]}>
                <View style={styles.toggleTextContainer}>
                  <View style={styles.toggleHeader}>
                    <AlertTriangle size={16} color="#f59e0b" />
                    <Text style={[styles.toggleTitle, { color: textColor }]}>
                      Device never confirms
                    </Text>
                  </View>
                  <Text style={[styles.toggleSubtitle, { color: subTextColor }]}>
                    st_no_confirm · write accepted but not confirmed (amber)
                  </Text>
                </View>
                <Switch
                  value={drillState.st_no_confirm}
                  onValueChange={(val) => handleToggle('st_no_confirm', val)}
                  trackColor={{ false: isDark ? '#3f3f46' : '#cbd5e1', true: '#f59e0b' }}
                  thumbColor="#ffffff"
                />
              </View>

              {/* 3. Slow SmartThings */}
              <View style={[styles.toggleRow, { backgroundColor: sectionBg, borderColor }]}>
                <View style={styles.toggleTextContainer}>
                  <View style={styles.toggleHeader}>
                    <Clock size={16} color="#06b6d4" />
                    <Text style={[styles.toggleTitle, { color: textColor }]}>
                      Slow SmartThings (3s)
                    </Text>
                  </View>
                  <Text style={[styles.toggleSubtitle, { color: subTextColor }]}>
                    st_latency · delays cloud calls by 3000 ms
                  </Text>
                </View>
                <Switch
                  value={drillState.st_latency}
                  onValueChange={(val) => handleToggle('st_latency', val)}
                  trackColor={{ false: isDark ? '#3f3f46' : '#cbd5e1', true: '#06b6d4' }}
                  thumbColor="#ffffff"
                />
              </View>

              {/* 4. Slow model */}
              <View style={[styles.toggleRow, { backgroundColor: sectionBg, borderColor }]}>
                <View style={styles.toggleTextContainer}>
                  <View style={styles.toggleHeader}>
                    <Brain size={16} color="#8b5cf6" />
                    <Text style={[styles.toggleTitle, { color: textColor }]}>
                      Slow model (4s)
                    </Text>
                  </View>
                  <Text style={[styles.toggleSubtitle, { color: subTextColor }]}>
                    model_slow · delays model by 4000 ms (filler covers wait)
                  </Text>
                </View>
                <Switch
                  value={drillState.model_slow}
                  onValueChange={(val) => handleToggle('model_slow', val)}
                  trackColor={{ false: isDark ? '#3f3f46' : '#cbd5e1', true: '#8b5cf6' }}
                  thumbColor="#ffffff"
                />
              </View>

              {/* 5. Model down */}
              <View style={[styles.toggleRow, { backgroundColor: sectionBg, borderColor }]}>
                <View style={styles.toggleTextContainer}>
                  <View style={styles.toggleHeader}>
                    <PowerOff size={16} color="#ef4444" />
                    <Text style={[styles.toggleTitle, { color: textColor }]}>
                      Model down
                    </Text>
                  </View>
                  <Text style={[styles.toggleSubtitle, { color: subTextColor }]}>
                    model_down · reasoning service unavailable
                  </Text>
                </View>
                <Switch
                  value={drillState.model_down}
                  onValueChange={(val) => handleToggle('model_down', val)}
                  trackColor={{ false: isDark ? '#3f3f46' : '#cbd5e1', true: '#ef4444' }}
                  thumbColor="#ffffff"
                />
              </View>
            </View>

            {/* Section 2: External Interference */}
            <View style={styles.section}>
              <Text style={[styles.sectionTitle, { color: textColor }]}>
                External Interference (Bypass Janus)
              </Text>
              <Text style={[styles.sectionSubtitle, { color: subTextColor }]}>
                Simulate another person changing a device directly via physical key or SmartThings app.
              </Text>

              <View style={styles.buttonCol}>
                <TouchableOpacity
                  onPress={handleExternalUnlock}
                  style={[styles.cmdBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9', borderColor }]}
                  activeOpacity={0.7}
                >
                  <LockOpen size={16} color="#f59e0b" />
                  <Text style={[styles.cmdBtnText, { color: textColor }]}>
                    Someone else unlocks the front door
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  onPress={handleExternalLock}
                  style={[styles.cmdBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9', borderColor }]}
                  activeOpacity={0.7}
                >
                  <Lock size={16} color="#10b981" />
                  <Text style={[styles.cmdBtnText, { color: textColor }]}>
                    …locks it
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  onPress={() => handleGarage('open')}
                  style={[styles.cmdBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9', borderColor }]}
                  activeOpacity={0.7}
                >
                  <LockOpen size={16} color="#f59e0b" />
                  <Text style={[styles.cmdBtnText, { color: textColor }]}>
                    Someone else opens the garage
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  onPress={() => handleGarage('close')}
                  style={[styles.cmdBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9', borderColor }]}
                  activeOpacity={0.7}
                >
                  <Lock size={16} color="#10b981" />
                  <Text style={[styles.cmdBtnText, { color: textColor }]}>
                    …closes it
                  </Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* Section: Janus speaks first */}
            <View style={styles.section}>
              <Text style={[styles.sectionTitle, { color: textColor }]}>
                Janus Speaks First
              </Text>
              <Text style={[styles.sectionSubtitle, { color: subTextColor }]}>
                Janus offers an action only if the device gives it something to offer.
              </Text>

              <View style={styles.buttonCol}>
                <TouchableOpacity
                  onPress={() => handleOffer('garage_open')}
                  style={[styles.cmdBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9', borderColor }]}
                  activeOpacity={0.7}
                >
                  <ShieldAlert size={16} color="#3b82f6" />
                  <Text style={[styles.cmdBtnText, { color: textColor }]}>
                    Offer: close the garage
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  onPress={() => handleOffer('door_unlocked')}
                  style={[styles.cmdBtn, { backgroundColor: isDark ? '#27272a' : '#f1f5f9', borderColor }]}
                  activeOpacity={0.7}
                >
                  <ShieldAlert size={16} color="#3b82f6" />
                  <Text style={[styles.cmdBtnText, { color: textColor }]}>
                    Offer: lock the front door
                  </Text>
                </TouchableOpacity>
              </View>
            </View>

            {/* Section 3: Reset All */}
            <View style={styles.section}>
              <TouchableOpacity
                onPress={handleResetAll}
                style={[
                  styles.resetBtn,
                  {
                    backgroundColor: isAnyActive ? '#ef4444' : isDark ? '#27272a' : '#f1f5f9',
                    borderColor: isAnyActive ? '#f87171' : borderColor,
                  },
                ]}
                activeOpacity={0.7}
              >
                <RotateCcw size={16} color={isAnyActive ? '#ffffff' : subTextColor} />
                <Text
                  style={[
                    styles.resetBtnText,
                    { color: isAnyActive ? '#ffffff' : subTextColor },
                  ]}
                >
                  All Off (Reset Drills)
                </Text>
              </TouchableOpacity>
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.65)',
    justifyContent: 'flex-end',
  },
  sheet: {
    maxHeight: '85%',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderBottomWidth: 0,
    paddingBottom: 24,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 18,
    paddingVertical: 16,
    borderBottomWidth: 1,
  },
  title: {
    fontSize: 17,
    fontWeight: '800',
    letterSpacing: -0.3,
  },
  topicBadge: {
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderWidth: 1,
    borderColor: 'rgba(245, 158, 11, 0.3)',
    borderRadius: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  topicText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#f59e0b',
  },
  closeBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  scroll: {
    maxHeight: 500,
  },
  scrollContent: {
    padding: 18,
    gap: 18,
  },
  description: {
    fontSize: 13,
    lineHeight: 18,
  },
  section: {
    gap: 10,
  },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  sectionSubtitle: {
    fontSize: 12,
    lineHeight: 16,
  },
  toggleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1,
  },
  toggleTextContainer: {
    flex: 1,
    paddingRight: 12,
  },
  toggleHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    marginBottom: 2,
  },
  toggleTitle: {
    fontSize: 14,
    fontWeight: '600',
  },
  toggleSubtitle: {
    fontSize: 11,
    fontWeight: '500',
  },
  buttonCol: {
    gap: 8,
  },
  cmdBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1,
  },
  cmdBtnText: {
    fontSize: 13,
    fontWeight: '600',
  },
  resetBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1,
  },
  resetBtnText: {
    fontSize: 13,
    fontWeight: '700',
  },
});
