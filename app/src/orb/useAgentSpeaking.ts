import { useEffect, useState } from 'react';
import { CaptionEntry } from '../types/janus';

/** Janus is "speaking" from when a new agent caption arrives until its text has plausibly been said. */
export const useAgentSpeaking = (captions: CaptionEntry[]): boolean => {
  const [speaking, setSpeaking] = useState(false);
  const last = captions[captions.length - 1];
  const lastId = last && last.sender === 'agent' ? last.id : null;
  const length = last ? last.text.length : 0;

  useEffect(() => {
    if (!lastId) {
      setSpeaking(false);
      return;
    }
    setSpeaking(true);
    const timer = setTimeout(() => setSpeaking(false), 900 + length * 55);
    return () => clearTimeout(timer);
  }, [lastId]);

  return speaking;
};
