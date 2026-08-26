const s = (size) => ({ width: size, height: size, viewBox: '0 0 24 24', fill: 'none' });

export const IconAX = ({ size = 20 }) => (
  <svg {...s(size)}>
    <rect width="24" height="24" rx="6" fill="var(--ax-orange)" />
    <path d="M6 18 L11 6 L14 6 L19 18 M8.4 13 L15.6 13" stroke="var(--ax-black)" strokeWidth="2.3" strokeLinecap="square" />
  </svg>
);

export const IconPlay = ({ size = 14 }) => (
  <svg {...s(size)}><path d="M7 4.5v15l13-7.5z" fill="currentColor" /></svg>
);

export const IconStop = ({ size = 14 }) => (
  <svg {...s(size)}><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" /></svg>
);

export const IconSend = ({ size = 15 }) => (
  <svg {...s(size)}><path d="M4 12h16M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

export const IconRefresh = ({ size = 14 }) => (
  <svg {...s(size)}>
    <path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconTrash = ({ size = 14 }) => (
  <svg {...s(size)}>
    <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconDownload = ({ size = 14 }) => (
  <svg {...s(size)}>
    <path d="M12 3v12M7 11l5 5 5-5M4 20h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconBrowser = ({ size = 16 }) => (
  <svg {...s(size)}>
    <rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" strokeWidth="1.8" />
    <path d="M3 9h18" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="6.5" cy="6.5" r="0.9" fill="currentColor" />
  </svg>
);

export const IconRobot = ({ size = 16 }) => (
  <svg {...s(size)}>
    <rect x="4" y="8" width="16" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
    <path d="M12 4v4M9 14h.01M15 14h.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

export const IconHistory = ({ size = 16 }) => (
  <svg {...s(size)}>
    <path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3.5 2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconTerminal = ({ size = 16 }) => (
  <svg {...s(size)}>
    <rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" strokeWidth="1.8" />
    <path d="M7 9l3 3-3 3M13 15h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconClock = ({ size = 14 }) => (
  <svg {...s(size)}>
    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
    <path d="M12 7v5l3 2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

export const IconHome = ({ size = 16 }) => (
  <svg {...s(size)}>
    <path d="M4 11l8-6 8 6v8a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 014 19z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
  </svg>
);

export const IconGrid = ({ size = 16 }) => (
  <svg {...s(size)}>
    <rect x="4" y="4" width="7" height="7" rx="1.8" stroke="currentColor" strokeWidth="1.8" />
    <rect x="13" y="4" width="7" height="7" rx="1.8" stroke="currentColor" strokeWidth="1.8" />
    <rect x="4" y="13" width="7" height="7" rx="1.8" stroke="currentColor" strokeWidth="1.8" />
    <rect x="13" y="13" width="7" height="7" rx="1.8" stroke="currentColor" strokeWidth="1.8" />
  </svg>
);

export const IconShield = ({ size = 16 }) => (
  <svg {...s(size)}>
    <path d="M12 3l7 3v6c0 4-3 7.5-7 9-4-1.5-7-5-7-9V6z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
  </svg>
);

export const IconPlug = ({ size = 16 }) => (
  <svg {...s(size)}>
    <path d="M9 3v6M15 3v6M6 9h12v3a6 6 0 01-12 0zM12 18v3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconSearch = ({ size = 15 }) => (
  <svg {...s(size)}>
    <circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.9" />
    <path d="M16 16l4 4" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
  </svg>
);

export const IconSpark = ({ size = 14 }) => (
  <svg {...s(size)}>
    <path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" fill="currentColor" />
  </svg>
);

export const IconPlus = ({ size = 14 }) => (
  <svg {...s(size)}><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>
);

export const IconExpand = ({ size = 14 }) => (
  <svg {...s(size)}>
    <path d="M4 9V4h5M20 15v5h-5M20 9V4h-5M4 15v5h5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const IconArrowRight = ({ size = 14 }) => (
  <svg {...s(size)}><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
