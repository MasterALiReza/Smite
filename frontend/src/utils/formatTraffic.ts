/**
 * Format bytes/MB to human-readable format (MB, GB, TB, PB)
 * @param mb - Traffic in megabytes
 * @returns Formatted string with appropriate unit
 */
export function formatTraffic(mb: number): string {
  if (mb < 1024) {
    return `${mb.toFixed(2)} MB`
  }
  
  const gb = mb / 1024
  if (gb < 1024) {
    return `${gb.toFixed(2)} GB`
  }
  
  const tb = gb / 1024
  if (tb < 1024) {
    return `${tb.toFixed(2)} TB`
  }
  
  const pb = tb / 1024
  return `${pb.toFixed(2)} PB`
}

/**
 * Format bytes to human-readable format
 * @param bytes - Traffic in bytes
 * @returns Formatted string with appropriate unit
 */
export function formatBytes(bytes: number): string {
  const mb = bytes / (1024 * 1024)
  return formatTraffic(mb)
}

/**
 * Format traffic rate (MB per hour) to human-readable format
 * @param mbPerHour - Traffic rate in MB per hour
 * @returns Formatted string with appropriate unit
 */
export function formatTrafficRate(mbPerHour: number): string {
  return `${formatTraffic(mbPerHour)}/h`
}

