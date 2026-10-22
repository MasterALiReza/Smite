// ─── Tunnels Form Shared Pure Helpers ─────────────────────────────────────

/**
 * Parses comma-separated ports and port ranges (e.g., "80,443,10000-20000").
 * Used identically across GOST, Backhaul and other multi-port tunnel cores.
 */
export const parsePortsAndRanges = (portsStr: string): { ports: number[], port_ranges: string[] } => {
  const ports: number[] = []
  const port_ranges: string[] = []
  
  if (!portsStr) return { ports, port_ranges }

  portsStr.split(',').forEach(p => {
    const trimmed = p.trim()
    if (!trimmed) return
    if (trimmed.includes('-')) {
      const parts = trimmed.split('-')
      if (parts.length === 2) {
        const start = parseInt(parts[0].trim())
        const end = parseInt(parts[1].trim())
        if (!isNaN(start) && !isNaN(end) && start > 0 && end <= 65535 && start <= end) {
          port_ranges.push(`${start}-${end}`)
        }
      }
    } else {
      const num = parseInt(trimmed)
      if (!isNaN(num) && num > 0 && num <= 65535) {
        ports.push(num)
      }
    }
  })
  return { ports, port_ranges }
}

/**
 * Generates a standard RFC4122 v4 UUID string.
 */
export const generateUUID = (): string => {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

/**
 * Generates a random control port between 20000 and 45000.
 */
export const generateRandomControlPort = (): string => {
  return String(Math.floor(20000 + Math.random() * 25000))
}
