/** A directed route forming P: one spine, one return, one open continuation. */
export default function BrandMark({
  size = 32,
  animated = false,
}: {
  size?: number;
  animated?: boolean;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      className={animated ? 'brand-mark is-working' : 'brand-mark'}
    >
      <rect width="32" height="32" rx="7" fill="currentColor" />
      <path
        className="mark-route"
        d="M9 25V9H19C23 9 25 11 25 14C25 17 23 19 19 19H9"
        stroke="white"
        strokeWidth="2.8"
        strokeLinecap="square"
        strokeLinejoin="round"
      />
      <path d="M9 19H17" stroke="#8DA6FF" strokeWidth="2.8" />
      <rect x="6.5" y="16.5" width="5" height="5" rx="1" fill="#8DA6FF" />
    </svg>
  );
}
