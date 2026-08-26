// Country code to Flag emoji and localized names
export const countryNamesFa: Record<string, string> = {
  IR: 'ایران',
  DE: 'آلمان',
  TR: 'ترکیه',
  FI: 'فنلاند',
  NL: 'هلند',
  FR: 'فرانسه',
  GB: 'انگلستان',
  UK: 'انگلستان',
  US: 'آمریکا',
  CA: 'کانادا',
  RU: 'روسیه',
  SE: 'سوئد',
  CH: 'سوئیس',
  AT: 'اتریش',
  PL: 'لهستان',
  IT: 'ایتالیا',
  ES: 'اسپانیا',
  AE: 'امارات',
  SG: 'سنگاپور',
  JP: 'ژاپن',
  KR: 'کره جنوبی',
  IN: 'هند',
  AU: 'استرالیا',
  NO: 'نروژ',
  DK: 'دانمارک',
  BE: 'بلژیک',
  CZ: 'جمهوری چک',
  RO: 'رومانی',
  BG: 'بلغارستان',
  HU: 'مجارستان',
  GR: 'یونان',
  UA: 'اوکراین',
  AM: 'ارمنستان',
  AZ: 'آذربایجان',
  GE: 'گرجستان',
}

// Convert 2-letter ISO country code (e.g. 'DE') to Flag Emoji (🇩🇪)
export function getCountryFlag(code?: string): string {
  if (!code || typeof code !== 'string') return '🌐'
  const cleanCode = code.trim().toUpperCase()
  if (cleanCode.length !== 2) return '🌐'
  
  try {
    const codePoints = cleanCode
      .split('')
      .map((char) => 127397 + char.charCodeAt(0))
    return String.fromCodePoint(...codePoints)
  } catch {
    return '🌐'
  }
}

// Translate English node name (e.g. 'DE Node 1') to Persian (e.g. 'نود آلمان ۱') if in Persian UI
export function formatLocalizedNodeName(name: string, isPersian: boolean, countryCode?: string): string {
  if (!name) return name
  if (!isPersian) return name

  // Check pattern: "XX Node Y" or "XX-Node-Y"
  const match = name.match(/^([A-Za-z]{2})[\s\-_]Node[\s\-_](\d+)$/i)
  if (match) {
    const cc = match[1].toUpperCase()
    const num = match[2]
    const faCountry = countryNamesFa[cc] || cc
    return `نود ${faCountry} ${num}`
  }

  // If node name matches "node-1" or "node 1"
  const simpleMatch = name.match(/^node[\s\-_](\d+)$/i)
  if (simpleMatch) {
    const num = simpleMatch[1]
    if (countryCode && countryNamesFa[countryCode.toUpperCase()]) {
      return `نود ${countryNamesFa[countryCode.toUpperCase()]} ${num}`
    }
    return `نود ${num}`
  }

  return name
}
