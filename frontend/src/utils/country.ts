import React from 'react'

// Country code to localized names in Persian
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

// Robust country code extractor from metadata or server name
export function extractCountryCode(name?: string, metadataCode?: string, fallbackRole?: string): string {
  if (metadataCode && typeof metadataCode === 'string' && metadataCode.trim().length === 2) {
    return metadataCode.trim().toUpperCase()
  }
  if (!name) {
    return fallbackRole === 'iran' ? 'IR' : ''
  }
  
  const upper = name.toUpperCase()
  
  // 1. Explicit country keywords (highest priority)
  if (upper.includes('IRAN') || upper.startsWith('IR-') || upper.startsWith('IR_') || upper.startsWith('IR ')) return 'IR'
  if (upper.includes('TURKEY') || upper.startsWith('TR-') || upper.startsWith('TR_') || upper.startsWith('TR ')) return 'TR'
  if (upper.includes('GERMANY') || upper.includes('DEUTSCH') || upper.startsWith('DE-') || upper.startsWith('DE_') || upper.startsWith('DE ')) return 'DE'
  if (upper.includes('FINLAND') || upper.startsWith('FI-') || upper.startsWith('FI_') || upper.startsWith('FI ') || upper.startsWith('FN-')) return 'FI'
  if (upper.includes('USA') || upper.includes('UNITED STATES') || upper.startsWith('US-') || upper.startsWith('US_') || upper.startsWith('US ')) return 'US'
  if (upper.includes('NETHERLAND') || upper.startsWith('NL-') || upper.startsWith('NL_') || upper.startsWith('NL ')) return 'NL'
  if (upper.includes('FRANCE') || upper.startsWith('FR-') || upper.startsWith('FR_') || upper.startsWith('FR ')) return 'FR'
  if (upper.includes('ENGLAND') || upper.includes('BRITAIN') || upper.startsWith('GB-') || upper.startsWith('UK-')) return 'GB'
  if (upper.includes('RUSSIA') || upper.startsWith('RU-') || upper.startsWith('RU_') || upper.startsWith('RU ')) return 'RU'
  if (upper.includes('SWEDEN') || upper.startsWith('SE-') || upper.startsWith('SE_') || upper.startsWith('SE ')) return 'SE'
  if (upper.includes('SWITZERLAND') || upper.startsWith('CH-') || upper.startsWith('CH_') || upper.startsWith('CH ')) return 'CH'
  if (upper.includes('CANADA') || upper.startsWith('CA-') || upper.startsWith('CA_') || upper.startsWith('CA ')) return 'CA'
  if (upper.includes('AUSTRIA') || upper.startsWith('AT-') || upper.startsWith('AT_') || upper.startsWith('AT ')) return 'AT'
  if (upper.includes('POLAND') || upper.startsWith('PL-') || upper.startsWith('PL_') || upper.startsWith('PL ')) return 'PL'
  if (upper.includes('ITALY') || upper.startsWith('IT-') || upper.startsWith('IT_') || upper.startsWith('IT ')) return 'IT'
  if (upper.includes('SPAIN') || upper.startsWith('ES-') || upper.startsWith('ES_') || upper.startsWith('ES ')) return 'ES'
  if (upper.includes('DUBAI') || upper.includes('EMIRATES') || upper.startsWith('AE-') || upper.startsWith('AE_') || upper.startsWith('AE ')) return 'AE'
  if (upper.includes('SINGAPORE') || upper.startsWith('SG-') || upper.startsWith('SG_') || upper.startsWith('SG ')) return 'SG'
  if (upper.includes('JAPAN') || upper.startsWith('JP-') || upper.startsWith('JP_') || upper.startsWith('JP ')) return 'JP'
  if (upper.includes('KOREA') || upper.startsWith('KR-') || upper.startsWith('KR_') || upper.startsWith('KR ')) return 'KR'
  if (upper.includes('HETZ')) return 'DE'

  // 2. Token-aware match: check individual tokens separated by whitespace or hyphens
  const tokens = upper.split(/[\s\-_\/]+/)
  for (const token of tokens) {
    if (token in countryNamesFa) {
      return token
    }
  }

  // 3. Fallback to role if provided
  if (fallbackRole === 'iran') return 'IR'

  return ''
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
    const cc = countryCode?.toUpperCase() || ''
    if (cc && countryNamesFa[cc]) {
      return `نود ${countryNamesFa[cc]} ${num}`
    }
    return `نود ${num}`
  }

  return name
}
