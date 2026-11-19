/** The Fieldwork mark: a green tile with an "f" and a dot (the full stop in "fieldwork."). */
export default function Logo({ size = 24, className = '' }: { size?: number; className?: string }) {
  return <svg className={`brand-logo ${className}`.trim()} width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Fieldwork logo">
    <rect x="2" y="2" width="60" height="60" rx="16" fill="#327858" />
    <path d="M29 47V27.5C29 21.5 32.5 18 38.5 18" fill="none" stroke="#fff" strokeWidth="5.5" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M22.5 29H37" fill="none" stroke="#fff" strokeWidth="5.5" strokeLinecap="round" />
    <circle cx="43.5" cy="46" r="4.2" fill="#bfe6cd" />
  </svg>
}
