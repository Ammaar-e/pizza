import React from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  type TextInputProps,
  type TextStyle,
  View,
  type ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

export const colors = {
  brand: '#C2410C',
  brandSoft: '#FFF1E8',
  ink: '#111827',
  ink2: '#374151',
  muted: '#6B7280',
  line: '#E5E7EB',
  bg: '#F6F5F3',
  card: '#FFFFFF',
  ok: '#047857',
  okSoft: '#ECFDF5',
  warn: '#B45309',
  warnSoft: '#FFFBEB',
  bad: '#B91C1C',
  badSoft: '#FEF2F2',
  dark: '#1F2937',
};

type Variant = 'primary' | 'secondary' | 'danger' | 'success' | 'ghost' | 'dark';

export function Button({
  title,
  onPress,
  variant = 'primary',
  size = 'md',
  disabled,
  busy,
  style,
  testID,
}: {
  title: string;
  onPress?: () => void;
  variant?: Variant;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  disabled?: boolean;
  busy?: boolean;
  style?: ViewStyle;
  testID?: string;
}) {
  const bg: Record<Variant, string> = {
    primary: colors.brand,
    secondary: colors.card,
    danger: colors.bad,
    success: colors.ok,
    ghost: 'transparent',
    dark: colors.dark,
  };
  const fg = variant === 'secondary' || variant === 'ghost' ? colors.ink : '#fff';
  const pad = { sm: 8, md: 12, lg: 16, xl: 20 }[size];
  const font = { sm: 14, md: 16, lg: 18, xl: 20 }[size];
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      disabled={disabled || busy}
      onPress={onPress}
      style={({ pressed }) => [
        styles.btn,
        { backgroundColor: bg[variant], paddingVertical: pad, opacity: disabled ? 0.45 : pressed ? 0.8 : 1 },
        variant === 'secondary' && { borderWidth: 1, borderColor: colors.line },
        style,
      ]}
    >
      {busy ? <ActivityIndicator color={fg} /> : <Text style={{ color: fg, fontSize: font, fontWeight: '700' }}>{title}</Text>}
    </Pressable>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function H1({ children, style }: { children: React.ReactNode; style?: TextStyle }) {
  return <Text style={[{ fontSize: 26, fontWeight: '800', color: colors.ink }, style]}>{children}</Text>;
}
export function H2({ children, style }: { children: React.ReactNode; style?: TextStyle }) {
  return <Text style={[{ fontSize: 18, fontWeight: '700', color: colors.ink, marginBottom: 8 }, style]}>{children}</Text>;
}
export function Muted({ children, style }: { children: React.ReactNode; style?: TextStyle }) {
  return <Text style={[{ fontSize: 14, color: colors.muted }, style]}>{children}</Text>;
}

export function Field({ label, hint, ...props }: TextInputProps & { label: string; hint?: string }) {
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={styles.label}>{label}</Text>
      <TextInput placeholderTextColor="#9CA3AF" {...props} style={[styles.input, props.style]} />
      {hint ? <Muted style={{ marginTop: 4, fontSize: 12 }}>{hint}</Muted> : null}
    </View>
  );
}

export function Banner({ tone = 'info', children }: { tone?: 'info' | 'ok' | 'warn' | 'bad'; children: React.ReactNode }) {
  if (!children) return null;
  const map = {
    info: [colors.brandSoft, colors.ink2],
    ok: [colors.okSoft, colors.ok],
    warn: [colors.warnSoft, colors.warn],
    bad: [colors.badSoft, colors.bad],
  } as const;
  const [bg, fg] = map[tone];
  return (
    <View style={[styles.banner, { backgroundColor: bg, borderColor: fg + '33' }]}>
      {typeof children === 'string' ? <Text style={{ color: fg, fontSize: 15 }}>{children}</Text> : children}
    </View>
  );
}

export function Chip({ label, selected, onPress, tone }: { label: string; selected?: boolean; onPress?: () => void; tone?: 'ok' | 'bad' }) {
  const border = tone === 'bad' ? colors.bad : tone === 'ok' ? colors.ok : selected ? colors.brand : colors.line;
  return (
    <Pressable
      onPress={onPress}
      style={[styles.chip, { borderColor: border, backgroundColor: selected ? colors.brandSoft : colors.card }]}
    >
      <Text style={{ color: colors.ink, fontWeight: selected ? '700' : '500' }}>{label}</Text>
    </Pressable>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <View style={styles.segment}>
      {options.map(([v, label]) => (
        <Pressable key={v} onPress={() => onChange(v)} style={[styles.segmentItem, value === v && { backgroundColor: colors.card }]}>
          <Text style={{ fontWeight: value === v ? '700' : '500', color: colors.ink }}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export function Row({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[{ flexDirection: 'row', alignItems: 'center', gap: 8 }, style]}>{children}</View>;
}

export function Line({ label, value, bold, color }: { label: string; value: string; bold?: boolean; color?: string }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 2 }}>
      <Text style={{ color: color ?? colors.ink2, fontWeight: bold ? '800' : '400', fontSize: bold ? 18 : 15 }}>{label}</Text>
      <Text style={{ color: color ?? colors.ink, fontWeight: bold ? '800' : '500', fontSize: bold ? 18 : 15 }}>{value}</Text>
    </View>
  );
}

/** Full-screen sheet with a header and a close button. */
export function Sheet({ visible, title, onClose, children, footer }: { visible: boolean; title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose} supportedOrientations={['portrait', 'landscape']}>
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={styles.sheetHeader}>
          <Text style={{ fontSize: 20, fontWeight: '800', flex: 1, color: colors.ink }} numberOfLines={1}>
            {title}
          </Text>
          <Button title="Close" variant="ghost" size="sm" onPress={onClose} />
        </View>
        <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
          {children}
        </ScrollView>
        {footer ? <View style={styles.sheetFooter}>{footer}</View> : null}
      </SafeAreaView>
    </Modal>
  );
}

export function Empty({ text }: { text: string }) {
  return (
    <View style={{ padding: 32, alignItems: 'center' }}>
      <Muted>{text}</Muted>
    </View>
  );
}

const styles = StyleSheet.create({
  btn: { borderRadius: 12, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center', minHeight: 44 },
  card: { backgroundColor: colors.card, borderRadius: 16, padding: 16, borderWidth: 1, borderColor: colors.line },
  label: { fontSize: 13, fontWeight: '600', color: colors.ink2, marginBottom: 4 },
  input: {
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
    fontSize: 17,
    backgroundColor: '#fff',
    color: colors.ink,
  },
  banner: { borderRadius: 12, padding: 12, borderWidth: 1, marginBottom: 12 },
  chip: { borderWidth: 2, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 8, marginRight: 8, marginBottom: 8 },
  segment: { flexDirection: 'row', backgroundColor: '#E7E5E4', borderRadius: 12, padding: 4 },
  segmentItem: { flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 9 },
  sheetHeader: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.line, backgroundColor: colors.card },
  sheetFooter: { padding: 16, borderTopWidth: 1, borderColor: colors.line, backgroundColor: colors.card },
});
