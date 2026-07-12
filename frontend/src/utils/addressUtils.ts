/**
 * Parse an address:port string, handling both IPv4 and IPv6 addresses.
 * 
 * Supports formats:
 * - IPv4: "127.0.0.1:8080" -> { host: "127.0.0.1", port: 8080 }
 * - IPv6: "[2001:db8::1]:8080" -> { host: "2001:db8::1", port: 8080 }
 * - IPv6: "2001:db8::1" -> { host: "2001:db8::1", port: null }
 * - Hostname: "example.com:8080" -> { host: "example.com", port: 8080 }
 */
export function parseAddressPort(addressStr: string): { host: string; port: number | null } {
  if (!addressStr) {
    return { host: '', port: null }
  }

  const trimmed = addressStr.trim()

  const ipv6BracketMatch = trimmed.match(/^\[([^\]]+)\](?::(\d+))?$/)
  if (ipv6BracketMatch) {
    const host = ipv6BracketMatch[1]
    const portStr = ipv6BracketMatch[2]
    const port = portStr ? parseInt(portStr, 10) : null
    return { host, port: isNaN(port!) ? null : port }
  }

  const colonCount = (trimmed.match(/:/g) || []).length
  if (colonCount > 1 && !trimmed.includes('[')) {
    return { host: trimmed, port: null }
  }

  const lastColonIndex = trimmed.lastIndexOf(':')
  if (lastColonIndex > 0 && lastColonIndex < trimmed.length - 1) {
    const hostPart = trimmed.substring(0, lastColonIndex)
    const portStr = trimmed.substring(lastColonIndex + 1)
    const port = parseInt(portStr, 10)
    
    if (!isNaN(port)) {
      return { host: hostPart, port }
    }
  }

  return { host: trimmed, port: null }
}

/**
 * Format host and port into address:port string, handling IPv6 addresses.
 * 
 * @param host - Host address (IPv4, IPv6, or hostname)
 * @param port - Port number (optional)
 * @returns Formatted string: "host:port" or "[ipv6]:port" or "host"
 */
export function formatAddressPort(host: string, port: number | null | undefined): string {
  if (!host) {
    return ''
  }

  const isIPv6 = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/.test(host) || 
                 (host.includes(':') && !host.includes('.') && host.split(':').length > 2)

  if (isIPv6) {
    if (port !== null && port !== undefined) {
      return `[${host}]:${port}`
    }
    return host
  }

  if (port !== null && port !== undefined) {
    return `${host}:${port}`
  }
  return host
}

/**
 * Check if a string is a valid IP address (IPv4 or IPv6).
 */
export function isValidIPAddress(address: string): boolean {
  if (!address) return false

  // IPv4 pattern
  const ipv4Pattern = /^(\d{1,3}\.){3}\d{1,3}$/
  if (ipv4Pattern.test(address)) {
    const parts = address.split('.')
    return parts.every(part => {
      const num = parseInt(part, 10)
      return num >= 0 && num <= 255
    })
  }

  const ipv6Pattern = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/
  if (ipv6Pattern.test(address)) {
    return true
  }

  if (address.includes('::')) {
    const parts = address.split('::')
    if (parts.length === 2) {
      const left = parts[0].split(':').filter(p => p)
      const right = parts[1].split(':').filter(p => p)
      return (left.length + right.length) <= 7
    }
  }

  return false
}

/**
 * Check if a string is a valid IPv6 address.
 */
export function isValidIPv6Address(address: string): boolean {
  if (!address) return false

  const cleanAddress = address.replace(/^\[|\]$/g, '')

  const ipv6Pattern = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$/
  if (ipv6Pattern.test(cleanAddress)) {
    return true
  }

  if (cleanAddress.includes('::')) {
    const parts = cleanAddress.split('::')
    if (parts.length === 2) {
      const left = parts[0].split(':').filter(p => p)
      const right = parts[1].split(':').filter(p => p)
      return (left.length + right.length) <= 7
    }
  }

  return false
}

