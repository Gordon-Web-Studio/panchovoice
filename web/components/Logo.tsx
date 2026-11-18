export function LogoMark({ size = 28 }: { size?: number }) {
  // A speech bubble that holds a sound wave.
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <path
        d="M7 4h18a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5h-9.5l-6 5.2c-.6.5-1.5.1-1.5-.7V24H7a5 5 0 0 1-5-5V9a5 5 0 0 1 5-5Z"
        fill="var(--accent)"
        opacity="0.16"
      />
      <path
        d="M7 4h18a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5h-9.5l-6 5.2c-.6.5-1.5.1-1.5-.7V24H7a5 5 0 0 1-5-5V9a5 5 0 0 1 5-5Z"
        stroke="var(--accent)"
        strokeWidth="1.6"
      />
      <g stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round">
        <line x1="9" y1="12.5" x2="9" y2="15.5" />
        <line x1="12.5" y1="10" x2="12.5" y2="18" />
        <line x1="16" y1="8" x2="16" y2="20" />
        <line x1="19.5" y1="11" x2="19.5" y2="17" />
        <line x1="23" y1="13" x2="23" y2="15" />
      </g>
    </svg>
  );
}

export function PanchitoAvatar({ size = 28 }: { size?: number }) {
  // A friendly round face with headphones.
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <circle cx="16" cy="16" r="15" fill="var(--accent)" />
      <path d="M8 17a8 8 0 0 1 16 0" stroke="var(--accent-fg)" strokeWidth="2" strokeLinecap="round" />
      <rect x="6" y="16" width="4" height="6" rx="2" fill="var(--accent-fg)" />
      <rect x="22" y="16" width="4" height="6" rx="2" fill="var(--accent-fg)" />
      <circle cx="13" cy="18" r="1.4" fill="var(--accent-fg)" />
      <circle cx="19" cy="18" r="1.4" fill="var(--accent-fg)" />
      <path d="M13.5 21.5c1.4 1.1 3.6 1.1 5 0" stroke="var(--accent-fg)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
