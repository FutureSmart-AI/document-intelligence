/**
 * The credentials board's own glyphs.
 *
 * Drawn here rather than added to `components/Icon.tsx`: these four are used by
 * this screen and nothing else, and the shared set is the product's vocabulary
 * rather than a place to park a one-off.
 */

export const EYE = (
  <svg
    viewBox="0 0 20 20"
    width="15"
    height="15"
    fill="none"
    aria-hidden="true"
  >
    <path
      d="M1.8 10S4.8 4.8 10 4.8 18.2 10 18.2 10 15.2 15.2 10 15.2 1.8 10 1.8 10z"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <circle cx="10" cy="10" r="2.3" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);

export const EYE_OFF = (
  <svg
    viewBox="0 0 20 20"
    width="15"
    height="15"
    fill="none"
    aria-hidden="true"
  >
    <path
      d="M4.3 5.6C2.7 7 1.8 10 1.8 10s3 5.2 8.2 5.2c1.5 0 2.8-.4 3.9-1M8.2 5.1A7.5 7.5 0 0110 4.8c5.2 0 8.2 5.2 8.2 5.2s-.8 1.4-2.2 2.8"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
    <path
      d="M8.4 8.4a2.3 2.3 0 003.2 3.2M3 3l14 14"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  </svg>
);

export const PENCIL = (
  <svg
    viewBox="0 0 20 20"
    width="15"
    height="15"
    fill="none"
    aria-hidden="true"
  >
    <path
      d="M13.6 3.9l2.5 2.5L7.5 15 4 16l1-3.5z"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
  </svg>
);

export const TRASH = (
  <svg
    viewBox="0 0 20 20"
    width="15"
    height="15"
    fill="none"
    aria-hidden="true"
  >
    <path
      d="M5 6h10M8 6V4.5h4V6M6.5 6l.7 10.5h5.6L13.5 6"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
