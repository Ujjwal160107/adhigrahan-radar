import React, { useEffect, useRef, useState } from 'react';

interface ProcessingProps {
  surveyNo: string;
  village: string;
  ready: boolean;
  onComplete: () => void;
}

// The search itself is a real API round-trip (typically well under a second
// against the local SQLite build). This screen used to fake ~5s of scripted
// "steps" for work that had already run offline at build time - that was
// theatre, not progress. It now shows a plain spinner for exactly as long as
// the real request takes, with a short minimum so a fast response does not
// flash unreadably.
const MIN_VISIBLE_MS = 300;

export const Processing: React.FC<ProcessingProps> = ({ surveyNo, village, ready, onComplete }) => {
  const [minTimeElapsed, setMinTimeElapsed] = useState(false);
  const firedRef = useRef(false);

  useEffect(() => {
    const t = setTimeout(() => setMinTimeElapsed(true), MIN_VISIBLE_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (ready && minTimeElapsed && !firedRef.current) {
      firedRef.current = true;
      onComplete();
    }
  }, [ready, minTimeElapsed, onComplete]);

  return (
    <div className="w-full px-8 sm:px-16 md:px-20 pt-16 sm:pt-24 max-w-6xl mx-auto flex flex-col items-start">
      <div className="flex items-center gap-4 mb-6">
        <span className="w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin" />
        <h1 className="font-serif italic font-bold text-2xl sm:text-3xl text-black tracking-tight select-none">
          Querying the parcel index…
        </h1>
      </div>
      <p className="font-mono text-sm text-ink-muted max-w-2xl">
        Looking up{' '}
        <span className="text-black font-medium">{surveyNo || 'any survey number'}</span>
        {village ? (
          <>
            {' '}in <span className="text-black font-medium">{village}</span>
          </>
        ) : null}
        . Matching, scoring and evidence linkage already ran during the offline build -
        this request is a single database read.
      </p>
    </div>
  );
};
