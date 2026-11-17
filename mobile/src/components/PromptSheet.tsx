/** Cross-platform text prompt (window.prompt does not exist on Android/iOS). */
import React, { useEffect, useState } from 'react';
import { Button, Field, Muted, Sheet } from './ui';

export interface PromptRequest {
  title: string;
  message?: string;
  label: string;
  secure?: boolean;
  numeric?: boolean;
  initial?: string;
  confirm?: string;
  resolve: (value: string | null) => void;
}

export function PromptSheet({ req, onDone }: { req: PromptRequest | null; onDone: () => void }) {
  const [value, setValue] = useState('');
  useEffect(() => setValue(req?.initial ?? ''), [req]);
  if (!req) return null;
  const finish = (v: string | null) => {
    req.resolve(v);
    onDone();
  };
  return (
    <Sheet
      visible
      title={req.title}
      onClose={() => finish(null)}
      footer={<Button title={req.confirm ?? 'OK'} size="lg" disabled={!value.trim()} onPress={() => finish(value.trim())} />}
    >
      {req.message ? <Muted style={{ marginBottom: 12 }}>{req.message}</Muted> : null}
      <Field
        label={req.label}
        value={value}
        onChangeText={setValue}
        secureTextEntry={req.secure}
        keyboardType={req.numeric ? 'number-pad' : 'default'}
        autoFocus
        onSubmitEditing={() => value.trim() && finish(value.trim())}
      />
    </Sheet>
  );
}

/** Hook: `const ask = usePrompt(); const code = await ask({...})` and render `<PromptSheet {...ask.sheet} />`. */
export function usePrompt() {
  const [req, setReq] = useState<PromptRequest | null>(null);
  const ask = (r: Omit<PromptRequest, 'resolve'>) => new Promise<string | null>((resolve) => setReq({ ...r, resolve }));
  return { ask, sheet: { req, onDone: () => setReq(null) } };
}
