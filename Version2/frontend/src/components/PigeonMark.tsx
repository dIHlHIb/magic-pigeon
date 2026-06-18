/** Woodpigeon peeking in from the left — short thick neck, big round head, like the photo. */
export function PigeonMark() {
  return (
    <svg className="bird" viewBox="0 0 64 64" width="28" height="28" role="img" aria-label="pigeon">
      <defs>
        <linearGradient id="enchant" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="rgba(130,40,255,0)" />
          <stop offset="25%" stopColor="rgba(160,70,255,0.55)" />
          <stop offset="50%" stopColor="rgba(200,130,255,0.85)" />
          <stop offset="75%" stopColor="rgba(160,70,255,0.55)" />
          <stop offset="100%" stopColor="rgba(130,40,255,0)" />
          <animateTransform attributeName="gradientTransform" type="translate"
            values="-1.2 -1.2; 1.2 1.2" dur="2.2s" repeatCount="indefinite" />
        </linearGradient>
        <clipPath id="hc">
          <path d="M0 24 Q6 18 14 14 Q22 8 32 8 Q42 7 48 14 Q50 17 48 22 Q56 23 62 26 Q56 28 48 28 Q46 34 36 38 Q24 42 16 38 Q8 42 0 46Z" />
        </clipPath>
      </defs>

      {/* Back of neck — short, thick, enters from left */}
      <path d="M0 24 Q6 18 14 14"
        fill="none" stroke="currentColor" strokeWidth="1.8" opacity="0.85" />

      {/* Nape → crown → forehead */}
      <path d="M14 14 Q22 8 32 8 Q42 7 48 14"
        fill="none" stroke="currentColor" strokeWidth="1.8" opacity="0.85" />

      {/* Forehead → cere bulge */}
      <path d="M48 14 Q50 17 48 22"
        fill="none" stroke="currentColor" strokeWidth="1.8" opacity="0.85" />

      {/* Upper mandible — long gentle curve */}
      <path d="M48 22 Q56 23 62 26"
        fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />

      {/* Lower mandible — curves back */}
      <path d="M62 26 Q56 28 48 28"
        fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />

      {/* Chin → lower jaw */}
      <path d="M48 28 Q46 34 36 38"
        fill="none" stroke="currentColor" strokeWidth="1.7" opacity="0.85" />

      {/* Throat → neck front — exits at left */}
      <path d="M36 38 Q24 42 16 38 Q8 42 0 46"
        fill="none" stroke="currentColor" strokeWidth="1.7" opacity="0.85" />

      {/* Cere — fleshy beak base */}
      <path d="M47 18 Q50 17 49 22 Q50 26 48 28"
        fill="none" stroke="currentColor" strokeWidth="0.8" opacity="0.3" />

      {/* Nostril */}
      <ellipse cx="52" cy="24" rx="0.9" ry="0.5" fill="currentColor" opacity="0.28" />

      {/* Eye — large, with orbital ring like in photo */}
      <ellipse cx="34" cy="20" rx="4" ry="4.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="34" cy="20" r="2" fill="currentColor" />
      <circle cx="35" cy="18.8" r="0.7" fill="var(--bg)" />

      {/* Head contour — slight cheek roundness */}
      <path d="M26 12 Q32 7 40 10" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.2" />
      <path d="M26 32 Q32 34 40 32" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.15" />

      {/* White neck patch — the signature woodpigeon marking */}
      <path d="M6 32 Q12 28 20 29" fill="none" stroke="currentColor" strokeWidth="2.8" opacity="0.5" />
      <path d="M8 36 Q13 33 19 34" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.25" />

      {/* Iridescent feather hints below patch */}
      <path d="M4 40 Q10 38 16 38" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.15" />

      {/* Crown feather wisps — slightly ruffled */}
      <path d="M28 7 Q26 2 29 4" fill="none" stroke="currentColor" strokeWidth="0.9" opacity="0.3" />
      <path d="M36 6 Q38 2 38 5" fill="none" stroke="currentColor" strokeWidth="0.6" opacity="0.2" />

      {/* Enchantment shimmer */}
      <rect x="-2" y="0" width="70" height="64" fill="url(#enchant)" clipPath="url(#hc)"
        style={{ mixBlendMode: 'screen' }} />
      <rect x="-2" y="0" width="70" height="64" fill="url(#enchant)" clipPath="url(#hc)"
        opacity="0.5" style={{ mixBlendMode: 'color-dodge' }} />
    </svg>
  )
}
